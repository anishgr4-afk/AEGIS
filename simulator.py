"""Simulate wearable vitals and stream them to the console in real time.

Three simulated devices post readings to POST /api/telemetry on app.py:

  garmin     Watch, worn ALL the time. Sparse: heart rate every 15 s (every
             5 s while exercising), breathing rate and SpO2 every minute,
             skin temperature every 2 min. Noisier, with motion spikes, bad
             SpO2 readings and periods where the strap is loose.
  astroskin  Smart shirt, worn only SOME of the time (see --astroskin).
             Dense: heart rate every second from the ECG, breathing rate
             every 4 s, SpO2 every 5 s, skin temperature every 10 s,
             systolic blood pressure estimate every minute.
  cgm        Glucose patch. One reading every 5 minutes, with meal rises.

All times above are SIMULATED seconds; --speed sets how many pass per real
second. Simulated data only - not a real device integration.

Every crewmember in data/crew.json is simulated at once, each with their
own scenario (clinical.py), random seed and exercise slot. By default one
crewmember is unwell (the roster's default wearer, in hypovolemic_shock) and
the rest are nominal. Vitals drift towards a scenario over a few simulated
minutes, as a real illness would.

Run, with app.py already running:
    .venv/Scripts/python simulator.py
    .venv/Scripts/python simulator.py --crew MS2=acute_febrile,PLT=post_eva_dcs
    .venv/Scripts/python simulator.py --follow-console   # default wearer follows the console scenario
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from clinical import SCENARIOS

# Seconds of simulated time between readings.
CADENCE: dict[str, dict[str, int]] = {
    "astroskin": {"heart_rate_bpm": 1, "respiratory_rate_bpm": 4, "spo2_pct": 5, "skin_temp_c": 10, "bp_systolic_est": 60},
    "garmin": {"heart_rate_bpm": 15, "respiratory_rate_bpm": 60, "spo2_pct": 60, "skin_temp_c": 120},
    "cgm": {"blood_sugar_mgdl": 300},
}
GARMIN_HR_EXERCISE_CADENCE = 5  # watches sample faster during an activity

# Measurement noise (standard deviation). The shirt's ECG and chest bands are
# much cleaner than an optical wrist sensor.
NOISE: dict[tuple[str, str], float] = {
    ("astroskin", "heart_rate_bpm"): 1.0, ("garmin", "heart_rate_bpm"): 2.5,
    ("astroskin", "respiratory_rate_bpm"): 0.5, ("garmin", "respiratory_rate_bpm"): 1.5,
    ("astroskin", "spo2_pct"): 0.4, ("garmin", "spo2_pct"): 1.0,
    ("astroskin", "skin_temp_c"): 0.05, ("garmin", "skin_temp_c"): 0.15,
    ("astroskin", "bp_systolic_est"): 5.0,
    ("cgm", "blood_sugar_mgdl"): 3.0,
}

# Garmin artifacts (probability per reading).
GARMIN_HR_SPIKE_P = 0.02  # optical sensor locks onto arm movement
GARMIN_SPO2_BAD_P = 0.06  # poor wrist contact reads falsely low
GARMIN_LOOSE_START_P = 1 / 900  # per sim second: strap loose, no data
ASTROSKIN_ELECTRODE_LOSS_P = 1 / 1200  # per sim second: ECG contact lost

NOMINAL = SCENARIOS["nominal_transit"]["vitals"]
TAU_SCENARIO_S = 180.0  # vitals drift to a new scenario over a few minutes
TAU_EXERCISE_S = 60.0
EXERCISE_EFFECT = {"heart_rate_bpm": 45.0, "respiratory_rate_bpm": 10.0, "bp_systolic": 20.0}
SKIN_SITE_C = {"astroskin": 34.8, "garmin": 33.6}  # forehead runs warmer than wrist
SKIN_PER_CORE = 0.8  # skin follows core temperature, damped

GLUCOSE_BASELINE = 88.0  # matches generate_data.py
MEAL_EVERY_S = 5 * 3600
MEAL_FIRST_S = 1800
MEAL_PEAK_RISE = 25.0
MEAL_PEAK_AFTER_S = 2700.0  # ~45 min after eating

EXERCISE_PERIOD_S = 1800  # one session every 30 sim minutes...
EXERCISE_START_S = 900
EXERCISE_LENGTH_S = 480  # ...lasting 8 minutes
ASTROSKIN_MARGIN_S = 60  # shirt goes on a minute before, off a minute after
ASTROSKIN_STARTUP_S = 600  # run starts with a monitoring block, both devices on


class Simulator:
    """Physiology plus device models. No network code, so tests can run it."""

    def __init__(self, seed: int = 7, astroskin: str = "schedule", scenario: str = "nominal_transit",
                 schedule_offset: int = 0):
        if astroskin not in ("schedule", "always", "never"):
            raise ValueError("astroskin must be schedule, always or never")
        self.rng = random.Random(seed)
        self.astroskin_mode = astroskin
        self.schedule_offset = schedule_offset  # staggers exercise slots between crewmembers
        self.t = 0
        self.state = {k: float(v) for k, v in NOMINAL.items()}
        self.exercise_level = 0.0
        self.scenario = "nominal_transit"
        self.target = dict(NOMINAL)
        self.set_scenario(scenario)
        self.garmin_loose_until = -1
        self.electrode_loss_until = -1
        # Stagger readings so the watch's signals don't all land on one second.
        self.phase = {(d, s): self.rng.randrange(every) for d, sig in CADENCE.items() for s, every in sig.items()}

    def set_scenario(self, name: str) -> bool:
        """Follow a clinical.py scenario. Unknown names (e.g. manual_entry) keep the current one."""
        if name in SCENARIOS and name != self.scenario:
            self.scenario = name
            self.target = dict(SCENARIOS[name]["vitals"])
            return True
        return False

    # -- schedule ----------------------------------------------------------

    def exercising(self, t: int) -> bool:
        if self.scenario != "nominal_transit":
            return False  # an unwell crewmember is excused from exercise
        phase = (t + self.schedule_offset) % EXERCISE_PERIOD_S
        return EXERCISE_START_S <= phase < EXERCISE_START_S + EXERCISE_LENGTH_S

    def astroskin_worn(self, t: int) -> bool:
        if self.astroskin_mode != "schedule":
            return self.astroskin_mode == "always"
        if t < ASTROSKIN_STARTUP_S:
            return True
        if self.scenario != "nominal_transit":
            return True  # crew puts the shirt on for closer monitoring
        phase = (t + self.schedule_offset) % EXERCISE_PERIOD_S
        return EXERCISE_START_S - ASTROSKIN_MARGIN_S <= phase < EXERCISE_START_S + EXERCISE_LENGTH_S + ASTROSKIN_MARGIN_S

    # -- physiology --------------------------------------------------------

    def _step(self, exercising: bool) -> None:
        a = 1 - math.exp(-1 / TAU_SCENARIO_S)
        for k in self.state:
            self.state[k] += (float(self.target[k]) - self.state[k]) * a
        b = 1 - math.exp(-1 / TAU_EXERCISE_S)
        self.exercise_level += ((1.0 if exercising else 0.0) - self.exercise_level) * b

    def _glucose(self, t: int) -> float:
        rise = 0.0
        meal = MEAL_FIRST_S
        while meal <= t:
            x = (t - meal) / MEAL_PEAK_AFTER_S
            rise += MEAL_PEAK_RISE * x * math.exp(1 - x)  # peaks at MEAL_PEAK_RISE
            meal += MEAL_EVERY_S
        return GLUCOSE_BASELINE + rise

    def true_value(self, device: str, signal: str, t: int) -> float:
        s, e = self.state, self.exercise_level
        if signal == "heart_rate_bpm":
            daily = 2.0 * math.sin(2 * math.pi * t / 86400)  # lower at night
            return s["heart_rate_bpm"] + EXERCISE_EFFECT["heart_rate_bpm"] * e + daily
        if signal == "respiratory_rate_bpm":
            return s["respiratory_rate_bpm"] + EXERCISE_EFFECT["respiratory_rate_bpm"] * e
        if signal == "spo2_pct":
            return s["spo2_pct"]
        if signal == "bp_systolic_est":
            return s["bp_systolic"] + EXERCISE_EFFECT["bp_systolic"] * e
        if signal == "skin_temp_c":
            return SKIN_SITE_C[device] + SKIN_PER_CORE * (s["body_temp_c"] - float(NOMINAL["body_temp_c"]))
        if signal == "blood_sugar_mgdl":
            return self._glucose(t)
        raise ValueError(signal)

    def _measure(self, device: str, signal: str, t: int) -> float:
        value = self.true_value(device, signal, t) + self.rng.gauss(0, NOISE[(device, signal)])
        if device == "garmin":
            if signal == "heart_rate_bpm" and self.rng.random() < GARMIN_HR_SPIKE_P:
                value = self.rng.uniform(150, 185)
            elif signal == "spo2_pct" and self.rng.random() < GARMIN_SPO2_BAD_P:
                value = self.rng.uniform(84, 89)
        if signal == "spo2_pct":
            value = min(value, 100.0)
        decimals = {"skin_temp_c": 2, "respiratory_rate_bpm": 1}.get(signal, 0)
        return round(value, decimals) if decimals else float(round(value))

    # -- run ---------------------------------------------------------------

    def advance(self, seconds: int) -> list[dict[str, Any]]:
        """Advance the simulated clock and return every reading produced."""
        out: list[dict[str, Any]] = []
        for _ in range(seconds):
            self.t += 1
            t = self.t
            exercising = self.exercising(t)
            self._step(exercising)
            activity = "exercise" if exercising else "rest"

            if t >= self.garmin_loose_until and self.rng.random() < GARMIN_LOOSE_START_P:
                self.garmin_loose_until = t + self.rng.randint(60, 180)
            if t >= self.electrode_loss_until and self.rng.random() < ASTROSKIN_ELECTRODE_LOSS_P:
                self.electrode_loss_until = t + self.rng.randint(5, 20)

            worn = {
                "garmin": t >= self.garmin_loose_until,
                "astroskin": self.astroskin_worn(t),
                "cgm": True,
            }
            for device, signals in CADENCE.items():
                if not worn[device]:
                    continue
                for signal, every in signals.items():
                    if device == "garmin" and signal == "heart_rate_bpm" and exercising:
                        every = GARMIN_HR_EXERCISE_CADENCE
                    if (t + self.phase[(device, signal)]) % every:
                        continue
                    if (device == "astroskin" and signal in ("heart_rate_bpm", "respiratory_rate_bpm")
                            and t < self.electrode_loss_until):
                        continue
                    out.append({
                        "device": device, "signal": signal, "t": t,
                        "value": self._measure(device, signal, t), "activity": activity,
                    })
        return out


# -- network ----------------------------------------------------------------

def _request(url: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


CREW_FILE = Path(__file__).resolve().parent / "data" / "crew.json"
UNWELL_DEFAULT = "hypovolemic_shock"
SLOT_STAGGER_S = 480  # each crewmember's exercise slot starts 8 sim minutes after the last


def default_crew_scenarios(path: Path = CREW_FILE) -> dict[str, str]:
    """Roster order; the default wearer unwell, everyone else nominal."""
    data = json.loads(path.read_text(encoding="utf-8"))
    default = data.get("default")
    return {c["id"]: (UNWELL_DEFAULT if c["id"] == default else "nominal_transit") for c in data["crew"]}


def parse_crew(spec: str, roster: dict[str, str]) -> dict[str, str]:
    """'MS2=acute_febrile,PLT=post_eva_dcs' -> roster with those overridden."""
    out = {cid: "nominal_transit" for cid in roster}
    for part in filter(None, (x.strip() for x in spec.split(","))):
        cid, _, scenario = part.partition("=")
        if cid not in out:
            raise ValueError(f"unknown crew id '{cid}'; use: {', '.join(out)}")
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario '{scenario}'; use: {', '.join(sorted(SCENARIOS))}")
        out[cid] = scenario
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stream simulated wearable vitals for the whole crew to the console.")
    parser.add_argument("--url", default="http://127.0.0.1:5000", help="app.py base URL")
    parser.add_argument("--speed", type=int, default=5, help="simulated seconds per real second (default 5)")
    parser.add_argument("--astroskin", choices=("schedule", "always", "never"), default="schedule",
                        help="schedule: worn for exercise, monitoring and any non-nominal scenario")
    parser.add_argument("--crew", default=None,
                        help="scenario per crewmember, e.g. MS2=hypovolemic_shock,PLT=post_eva_dcs; "
                             "anyone not listed is nominal. Default: the roster's default wearer in "
                             f"{UNWELL_DEFAULT}, the rest nominal. Scenarios: {', '.join(sorted(SCENARIOS))}")
    parser.add_argument("--follow-console", action="store_true",
                        help="the roster's default wearer follows the console's clinical scenario")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--duration", type=float, default=0, help="real seconds to run (0 = until Ctrl+C)")
    parser.add_argument("--no-reset", action="store_true", help="keep readings already on the server")
    args = parser.parse_args(argv)
    if not 1 <= args.speed <= 120:
        parser.error("--speed must be between 1 and 120")

    roster = default_crew_scenarios()
    default_wearer = json.loads(CREW_FILE.read_text(encoding="utf-8")).get("default")
    try:
        scenarios = parse_crew(args.crew, roster) if args.crew else roster
    except ValueError as exc:
        parser.error(str(exc))

    sims = {cid: Simulator(seed=args.seed + i, astroskin=args.astroskin, scenario=scenario,
                           schedule_offset=i * SLOT_STAGGER_S)
            for i, (cid, scenario) in enumerate(scenarios.items())}

    base = args.url.rstrip("/")
    if not args.no_reset:
        try:
            _request(f"{base}/api/telemetry/reset", {})
        except (urllib.error.URLError, OSError) as exc:
            print(f"Cannot reach {base}: {exc}. Is app.py running?", file=sys.stderr)
            return 1

    print(f"Streaming to {base} at {args.speed}x | Astroskin: {args.astroskin} | Ctrl+C to stop")
    for cid, sim in sims.items():
        print(f"  {cid:<4} {sim.scenario}" + (" (follows console)" if args.follow_console and cid == default_wearer else ""))
    started = time.monotonic()
    last_poll = last_status = -1e9
    sent = 0
    urgency: dict[str, str] = {}
    offline = False
    try:
        while args.duration <= 0 or time.monotonic() - started < args.duration:
            tick = time.monotonic()
            if args.follow_console and default_wearer in sims and tick - last_poll >= 3:
                last_poll = tick
                try:
                    if sims[default_wearer].set_scenario(_request(f"{base}/api/clinical/state").get("scenario", "")):
                        print(f"  {default_wearer} scenario -> {sims[default_wearer].scenario}")
                except (urllib.error.URLError, OSError, ValueError):
                    pass
            batch = [{**sample, "crew": cid} for cid, sim in sims.items() for sample in sim.advance(args.speed)]
            try:
                if batch:
                    urgency.update(_request(f"{base}/api/telemetry", {"samples": batch}).get("crew", {}))
                    sent += len(batch)
                if offline:
                    print("  server reachable again")
                offline = False
            except (urllib.error.URLError, OSError, ValueError) as exc:
                if not offline:
                    print(f"  server not reachable ({exc}); readings dropped until it is back")
                offline = True
            if tick - last_status >= 10:
                last_status = tick
                any_sim = next(iter(sims.values()))
                crew_line = "  ".join(f"{cid} {urgency.get(cid, '?')}" for cid in sims)
                print(f"  sim {any_sim.t // 60:>4} min | {sent:>7} readings sent | triage: {crew_line}")
            time.sleep(max(0.0, 1.0 - (time.monotonic() - tick)))
    except KeyboardInterrupt:
        pass
    print(f"Stopped after {next(iter(sims.values())).t} simulated seconds, {sent} readings sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
