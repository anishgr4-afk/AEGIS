"""Wearable telemetry: ingest, rolling-window summary and triage.

Readings come from simulator.py. This is NOT a real device integration.

Devices
  astroskin  Smart shirt (CSA). Worn only for some periods. ECG-derived heart
             rate, breathing rate, SpO2 and skin temperature at the forehead,
             and a systolic blood pressure ESTIMATE. Dense: heart rate ~1 Hz.
  garmin     Wrist watch, worn all the time. Optical heart rate, estimated
             breathing rate, wrist SpO2 and wrist skin temperature. Sparse,
             noisier, prone to motion artifacts.
  cgm        Continuous glucose monitor patch. One reading every 5 minutes.

Rules this module follows
  - Triage uses the median of a rolling window per signal, never one
    reading, so a single motion spike cannot raise an alert.
  - When two devices report the same signal, the Astroskin wins.
  - Heart rate, breathing rate and blood pressure rules pause during
    exercise, and stay paused until every reading in the window was taken at
    least RECOVERY_S after it - elevated values are expected until then.
  - Skin temperature is not body temperature. The core-temperature fever
    rule in clinical.py is never applied to it. A rise against that device's
    own baseline prompts a thermometer recheck instead.
  - A signal with no recent reading is reported as "not measured", never as
    normal.

Thresholds come from clinical.evaluate_vitals, so manual entry and wearables
use one set of numbers. Simulated data only. Not a medical device.
"""
from __future__ import annotations

import json
import math
import statistics
import time
from collections import deque
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from clinical import VITAL_RANGES, evaluate_vitals

DEVICES: dict[str, dict[str, Any]] = {
    "astroskin": {
        "label": "Astroskin smart shirt",
        "signals": ("heart_rate_bpm", "respiratory_rate_bpm", "spo2_pct", "skin_temp_c", "bp_systolic_est"),
        "offline_after_s": 30,
    },
    "garmin": {
        "label": "Garmin watch",
        "signals": ("heart_rate_bpm", "respiratory_rate_bpm", "spo2_pct", "skin_temp_c"),
        "offline_after_s": 180,
    },
    "cgm": {
        "label": "Glucose patch (CGM)",
        "signals": ("blood_sugar_mgdl",),
        "offline_after_s": 900,
    },
}

# Median over readings in window_s. If fewer than min_samples fall inside it
# (sparse watch data), use the last min_samples within stale_s instead. With
# fewer than min_samples available at all, the signal is NOT judged - a
# median of two readings is just their average, so one motion spike could
# drag it over a threshold. (CGM readings arrive pre-filtered, so one will do.)
SIGNALS: dict[str, dict[str, Any]] = {
    "heart_rate_bpm": {"label": "Heart rate", "unit": "bpm", "window_s": 60, "stale_s": 180, "min_samples": 5, "prefer": ("astroskin", "garmin")},
    "respiratory_rate_bpm": {"label": "Breathing rate", "unit": "breaths/min", "window_s": 60, "stale_s": 300, "min_samples": 5, "prefer": ("astroskin", "garmin")},
    "spo2_pct": {"label": "SpO₂", "unit": "%", "window_s": 60, "stale_s": 360, "min_samples": 5, "prefer": ("astroskin", "garmin")},
    "bp_systolic_est": {"label": "Systolic blood pressure (estimate)", "unit": "mmHg", "window_s": 300, "stale_s": 600, "min_samples": 3, "prefer": ("astroskin",)},
    "blood_sugar_mgdl": {"label": "Blood sugar", "unit": "mg/dL", "window_s": 900, "stale_s": 1200, "min_samples": 1, "prefer": ("cgm",)},
    "skin_temp_c": {"label": "Skin temperature", "unit": "°C", "window_s": 300, "stale_s": 900, "min_samples": 3, "prefer": ("astroskin", "garmin")},
}

# Highest severity a source may raise ON ITS OWN. Wrist SpO2 is unreliable
# (poor contact reads falsely low), so it can prompt an oximeter recheck but
# cannot escalate to "act" by itself. Project decision - remove the entry to
# let it escalate.
SOURCE_MAX_SEVERITY: dict[tuple[str, str], str] = {
    ("garmin", "spo2_pct"): "watch",
}
SOURCE_RECHECK = {
    ("garmin", "spo2_pct"): "wrist SpO₂ cannot escalate on its own - recheck with the oximeter (Medical Diagnostic Pack)",
}

# Anything outside these is a sensor fault, not a measurement: dropped and counted.
PLAUSIBLE: dict[str, tuple[float, float]] = {
    "heart_rate_bpm": VITAL_RANGES["heart_rate_bpm"],
    "respiratory_rate_bpm": VITAL_RANGES["respiratory_rate_bpm"],
    "spo2_pct": VITAL_RANGES["spo2_pct"],
    "bp_systolic_est": VITAL_RANGES["bp_systolic"],
    "skin_temp_c": (20.0, 42.0),
    "blood_sugar_mgdl": (20.0, 600.0),
}

EXERCISE_PAUSED = ("heart_rate_bpm", "respiratory_rate_bpm", "bp_systolic_est")
RECOVERY_S = 180  # rates stay up after exercise; keep rules paused this long
SKIN_BASELINE_SAMPLES = 6  # resting readings per device before its baseline is set
SKIN_RISE_WATCH_C = 1.0  # project assumption, not a sourced threshold
FEED_OFFLINE_WALL_S = 10  # no POST for this long (real seconds) = simulator stopped
RESTART_GAP_S = 60  # sim clock jumped back this far = simulator restarted
BUFFER_LEN = 4000  # ~1 h of 1 Hz readings per device/signal
MAX_BATCH = 5000
MAX_SERIES = 2000

DISCLAIMER = "Simulated wearable data. Not a medical device. Demo thresholds only."


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class TelemetryStore:
    """In-memory, thread-safe. Not persisted: readings arrive every second and
    would rewrite the data files constantly. Restarting Flask clears it."""

    def __init__(
        self,
        on_urgency_change: Callable[[str | None, str, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._lock = RLock()
        self._on_change = on_urgency_change
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._buf: dict[tuple[str, str], deque] = {}
            self._sim_now: float | None = None
            self._last_seen: dict[str, float] = {}
            self._last_exercise: dict[str, float] = {}
            self._last_post_wall: float | None = None
            self._accepted = 0
            self._rejected = 0
            self._skin_seed: dict[str, list[float]] = {}
            self._skin_baseline: dict[str, float] = {}
            self._urgency: str | None = None

    # -- ingest ------------------------------------------------------------

    @staticmethod
    def _validate(samples: Any) -> list[tuple[str, str, float, float, str]]:
        """Check the whole batch before storing any of it."""
        if not isinstance(samples, list) or len(samples) > MAX_BATCH:
            raise ValueError(f"samples must be a list of at most {MAX_BATCH} readings")
        out = []
        for s in samples:
            if not isinstance(s, dict):
                raise ValueError("each sample must be a JSON object")
            device, signal = s.get("device"), s.get("signal")
            if device not in DEVICES:
                raise ValueError(f"device must be one of: {', '.join(DEVICES)}")
            if signal not in DEVICES[device]["signals"]:
                raise ValueError(f"{device} does not report '{signal}'")
            t, value = s.get("t"), s.get("value")
            if not _finite(t) or t < 0 or not _finite(value):
                raise ValueError("t (sim seconds, >= 0) and value must be finite numbers")
            activity = s.get("activity", "rest")
            if activity not in ("rest", "exercise"):
                raise ValueError("activity must be 'rest' or 'exercise'")
            out.append((device, signal, float(t), float(value), activity))
        return out

    def ingest(self, samples: Any) -> dict[str, Any]:
        batch = self._validate(samples)
        with self._lock:
            if batch and self._sim_now is not None and min(b[2] for b in batch) < self._sim_now - RESTART_GAP_S:
                self.reset()  # simulator restarted with a fresh clock
            accepted = rejected = 0
            for device, signal, t, value, activity in batch:
                self._sim_now = t if self._sim_now is None else max(self._sim_now, t)
                self._last_seen[device] = max(self._last_seen.get(device, t), t)
                if activity == "exercise":
                    self._last_exercise[device] = max(self._last_exercise.get(device, t), t)
                low, high = PLAUSIBLE[signal]
                if not low <= value <= high:
                    rejected += 1
                    continue
                self._buf.setdefault((device, signal), deque(maxlen=BUFFER_LEN)).append((t, value, activity))
                if signal == "skin_temp_c" and activity == "rest" and device not in self._skin_baseline:
                    seed = self._skin_seed.setdefault(device, [])
                    seed.append(value)
                    if len(seed) >= SKIN_BASELINE_SAMPLES:
                        self._skin_baseline[device] = statistics.median(seed)
                accepted += 1
            self._accepted += accepted
            self._rejected += rejected
            self._last_post_wall = self._clock()
            summary = self._summary_locked()
            new = summary["triage"]["urgency"]
            old, self._urgency = self._urgency, new

        # Outside our lock: the callback takes the app's lock and writes a file.
        if self._on_change and old != new and not (old is None and new == "routine"):
            flags = summary["triage"]["flags"]
            detail = "; ".join(f"{f['label']} {f['value']} {f['unit']}" for f in flags[:3]) or "no flags"
            self._on_change(old, new, detail)
        return {"accepted": accepted, "rejected_implausible": rejected, "urgency": new}

    # -- summary -----------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return self._summary_locked()

    def _window(self, device: str, signal: str) -> list[tuple[float, float, str]]:
        buf = self._buf.get((device, signal))
        if not buf or self._sim_now is None:
            return []
        spec = SIGNALS[signal]
        need = spec["min_samples"]
        recent = [p for p in buf if self._sim_now - p[0] <= spec["stale_s"]]
        in_window = [p for p in recent if self._sim_now - p[0] <= spec["window_s"]]
        return in_window if len(in_window) >= need else recent[-need:]

    def _context(self, device: str, points: list) -> str:
        if sum(1 for p in points if p[2] == "exercise") * 2 > len(points):
            return "exercise"
        # Still "recovery" while ANY reading in the window predates the end of
        # recovery - not merely while the clock says so. Sparse watch signals
        # (one breathing-rate reading a minute) reach back several minutes, so
        # a clock-only check let workout readings set off alarms at rest.
        last = self._last_exercise.get(device)
        if last is not None and any(p[0] < last + RECOVERY_S for p in points):
            return "recovery"
        return "rest"

    def _signal_summary(self, signal: str, devices: dict) -> dict[str, Any]:
        spec = SIGNALS[signal]
        too_few: tuple[str, int] | None = None
        for device in spec["prefer"]:
            if not devices[device]["online"]:
                continue
            pts = self._window(device, signal)
            if not pts:
                continue
            if len(pts) < spec["min_samples"]:
                too_few = too_few or (device, len(pts))
                continue  # a less-preferred device may have enough
            values = [p[1] for p in pts]
            return {
                "status": "measured",
                "value": round(statistics.median(values), 1),
                "unit": spec["unit"],
                "source": device,
                "source_label": DEVICES[device]["label"],
                "samples": len(pts),
                "min": round(min(values), 1),
                "max": round(max(values), 1),
                "age_s": round(self._sim_now - pts[-1][0], 1),
                "context": self._context(device, pts),
            }
        sources = [DEVICES[d]["label"] for d in spec["prefer"]]
        if too_few:
            device, n = too_few
            reason = f"only {n} recent {DEVICES[device]['label']} reading(s), need {spec['min_samples']} to judge"
        elif spec["prefer"] == ("astroskin",) and not devices["astroskin"]["online"]:
            reason = "Astroskin not being worn"
        else:
            reason = f"no recent reading from {' or '.join(sources)}"
        return {"status": "not_measured", "value": None, "unit": spec["unit"], "source": None, "reason": reason}

    def _summary_locked(self) -> dict[str, Any]:
        now = self._sim_now
        devices = {}
        for name, spec in DEVICES.items():
            seen = self._last_seen.get(name)
            age = None if seen is None or now is None else now - seen
            devices[name] = {
                "label": spec["label"],
                "online": age is not None and age <= spec["offline_after_s"],
                "last_seen_s_ago": None if age is None else round(age, 1),
            }
        signals = {sig: self._signal_summary(sig, devices) for sig in SIGNALS}
        feed_age = None if self._last_post_wall is None else self._clock() - self._last_post_wall
        return {
            "sim_time_s": now,
            "feed": {
                "online": feed_age is not None and feed_age <= FEED_OFFLINE_WALL_S,
                "last_post_s_ago": None if feed_age is None else round(feed_age, 1),
                "accepted": self._accepted,
                "rejected_implausible": self._rejected,
            },
            "devices": devices,
            "signals": signals,
            "triage": self._triage(signals),
            "disclaimer": DISCLAIMER,
        }

    # -- triage ------------------------------------------------------------

    def _triage(self, signals: dict[str, dict]) -> dict[str, Any]:
        vitals: dict[str, int] = {}
        not_evaluated: list[str] = []
        key_for = {
            "heart_rate_bpm": "heart_rate_bpm",
            "respiratory_rate_bpm": "respiratory_rate_bpm",
            "spo2_pct": "spo2_pct",
            "bp_systolic_est": "bp_systolic",
            "blood_sugar_mgdl": "blood_sugar_mgdl",
        }
        for signal, key in key_for.items():
            s = signals[signal]
            label = SIGNALS[signal]["label"]
            if s["status"] != "measured":
                not_evaluated.append(f"{label}: not measured - {s['reason']}")
            elif signal in EXERCISE_PAUSED and s["context"] != "rest":
                not_evaluated.append(f"{label}: {s['value']:g} {s['unit']} during {s['context']} - rules paused until rested")
            else:
                vitals[key] = round(s["value"])

        result = evaluate_vitals(vitals)
        flag_signal = {
            "heart_rate_bpm": "heart_rate_bpm", "respiratory_rate_bpm": "respiratory_rate_bpm",
            "spo2_pct": "spo2_pct", "blood_pressure": "bp_systolic_est", "blood_sugar_mgdl": "blood_sugar_mgdl",
        }
        flags = result["flags"]
        for f in flags:
            signal = flag_signal[f["id"]]
            s = signals[signal]
            f["source"] = s["source_label"]
            f["samples"] = s["samples"]
            cap = SOURCE_MAX_SEVERITY.get((s["source"], signal))
            if cap and f["severity"] == "act" and cap == "watch":
                f["severity"] = "watch"
                f["capped_from"] = "act"
            if (s["source"], signal) in SOURCE_RECHECK:
                f["threshold"] += " · " + SOURCE_RECHECK[(s["source"], signal)]

        skin = signals["skin_temp_c"]
        if skin["status"] != "measured":
            not_evaluated.append(f"Skin temperature: not measured - {skin['reason']}")
        else:
            base = self._skin_baseline.get(skin["source"])
            if base is None:
                not_evaluated.append(f"Skin temperature: still learning the {skin['source_label']} baseline")
            else:
                rise = skin["value"] - base
                skin["baseline"] = round(base, 2)
                skin["change_vs_baseline"] = round(rise, 2)
                if skin["context"] == "exercise":
                    not_evaluated.append("Skin temperature: exercising - rise check paused")
                elif rise >= SKIN_RISE_WATCH_C:
                    flags.append({
                        "id": "skin_temp_c", "label": "Skin temperature rise",
                        "value": f"+{rise:.1f}", "unit": "°C vs baseline", "severity": "watch",
                        "threshold": (
                            f"≥ +{SKIN_RISE_WATCH_C:g} °C vs this device's baseline · project assumption · "
                            "confirm core temperature with the thermometer (Medical Diagnostic Pack)"
                        ),
                        "source": skin["source_label"], "samples": skin["samples"],
                    })

        urgency = "act" if any(f["severity"] == "act" for f in flags) else "watch" if flags else "routine"
        return {
            "urgency": urgency,
            "flags": flags,
            "not_evaluated": not_evaluated,
            "note": "No wearable measures core body temperature; the fever rule needs a thermometer reading.",
        }

    # -- series ------------------------------------------------------------

    def series(self, signal: str, device: str | None = None, seconds: float = 600) -> list[dict]:
        with self._lock:
            if self._sim_now is None:
                return []
            out = []
            for dev in ((device,) if device else tuple(DEVICES)):
                for t, value, activity in self._buf.get((dev, signal), ()):
                    if self._sim_now - t <= seconds:
                        out.append({"t": t, "value": value, "device": dev, "activity": activity})
            out.sort(key=lambda p: p["t"])
            return out[-MAX_SERIES:]


URGENCY_RANK = {"act": 0, "watch": 1, "routine": 2}
CREW_FILE = Path(__file__).resolve().parent / "data" / "crew.json"


def load_crew(path: Path = CREW_FILE) -> tuple[list[dict], str]:
    """Roster and the default wearer (readings with no crew id go to them)."""
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    crew = [{"id": c["id"], "role": c.get("role", c["id"]), "name": c.get("name", "")} for c in data["crew"]]
    default = data.get("default", crew[0]["id"])
    if default not in {c["id"] for c in crew}:
        raise ValueError(f"crew.json default '{default}' is not in the roster")
    return crew, default


class CrewTelemetry:
    """One TelemetryStore per crewmember. Each store keeps its own clock,
    buffers and triage, so one person's readings never affect another's."""

    def __init__(
        self,
        crew: list[dict],
        default: str,
        on_urgency_change: Callable[[str | None, str, str], None] | None = None,
    ):
        self.crew = crew
        self.default = default
        self.stores: dict[str, TelemetryStore] = {}
        for member in crew:
            cid = member["id"]
            hook = None
            if on_urgency_change:
                def hook(old, new, detail, cid=cid):
                    on_urgency_change(old, new, f"{cid}: {detail}")
            self.stores[cid] = TelemetryStore(hook)

    def store(self, crew_id: str | None = None) -> TelemetryStore:
        cid = crew_id or self.default
        if cid not in self.stores:
            raise KeyError(f"crew must be one of: {', '.join(self.stores)}")
        return self.stores[cid]

    def summary(self, crew_id: str | None = None) -> dict[str, Any]:
        return self.store(crew_id).summary()

    def ingest(self, samples: Any) -> dict[str, Any]:
        """Samples may carry "crew"; without it they belong to the default wearer.
        The whole batch is checked before any of it is stored."""
        if not isinstance(samples, list) or len(samples) > MAX_BATCH:
            raise ValueError(f"samples must be a list of at most {MAX_BATCH} readings")
        groups: dict[str, list] = {}
        for s in samples:
            cid = s.get("crew", self.default) if isinstance(s, dict) else self.default
            if cid not in self.stores:
                raise ValueError(f"crew must be one of: {', '.join(self.stores)}")
            groups.setdefault(cid, []).append(s)
        for cid, batch in groups.items():
            TelemetryStore._validate(batch)
        results = {cid: self.stores[cid].ingest(batch) for cid, batch in groups.items()}
        urgencies = [r["urgency"] for r in results.values()]
        return {
            "accepted": sum(r["accepted"] for r in results.values()),
            "rejected_implausible": sum(r["rejected_implausible"] for r in results.values()),
            "urgency": min(urgencies, key=URGENCY_RANK.get) if urgencies else None,
            "crew": {cid: r["urgency"] for cid, r in results.items()},
        }

    def reset(self) -> None:
        for store in self.stores.values():
            store.reset()

    def all(self) -> list[dict[str, Any]]:
        return [{**member, "telemetry": self.stores[member["id"]].summary()} for member in self.crew]


def create_telemetry_blueprint(
    on_urgency_change: Callable[[str | None, str, str], None] | None = None,
    crew: list[dict] | None = None,
    default: str | None = None,
) -> Blueprint:
    if crew is None:
        crew, file_default = load_crew()
        default = default or file_default
    team = CrewTelemetry(crew, default or crew[0]["id"], on_urgency_change)
    bp = Blueprint("telemetry", __name__)
    bp.crew = team  # type: ignore[attr-defined]
    bp.store = team.store()  # type: ignore[attr-defined]  # default wearer, kept for older callers and tests

    def crew_arg():
        return request.args.get("crew") or None

    @bp.post("/api/telemetry")
    def ingest_telemetry():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object with a 'samples' list"}), 400
        try:
            return jsonify(team.ingest(payload.get("samples")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @bp.get("/api/telemetry/latest")
    def telemetry_latest():
        try:
            return jsonify(team.summary(crew_arg()))
        except KeyError as exc:
            return jsonify({"error": exc.args[0]}), 400

    @bp.get("/api/telemetry/crew")
    def telemetry_crew():
        return jsonify({"default": team.default, "crew": team.all()})

    @bp.get("/api/telemetry/series")
    def telemetry_series():
        signal = request.args.get("signal")
        device = request.args.get("device") or None
        if signal not in SIGNALS:
            return jsonify({"error": f"signal must be one of: {', '.join(SIGNALS)}"}), 400
        if device is not None and device not in DEVICES:
            return jsonify({"error": f"device must be one of: {', '.join(DEVICES)}"}), 400
        try:
            seconds = float(request.args.get("seconds", 600))
        except ValueError:
            return jsonify({"error": "seconds must be a number"}), 400
        if not math.isfinite(seconds) or not 1 <= seconds <= 3600:
            return jsonify({"error": "seconds must be between 1 and 3600"}), 400
        try:
            store = team.store(crew_arg())
        except KeyError as exc:
            return jsonify({"error": exc.args[0]}), 400
        return jsonify({"signal": signal, "points": store.series(signal, device, seconds)})

    @bp.post("/api/telemetry/reset")
    def telemetry_reset():
        team.reset()
        return jsonify({"ok": True})

    return bp
