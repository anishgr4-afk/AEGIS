"""Tests for wearable telemetry, the simulator, and evaluate_vitals changes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from clinical import SCENARIOS, evaluate_vitals  # noqa: E402
from simulator import CADENCE, Simulator  # noqa: E402
from telemetry import (  # noqa: E402
    FEED_OFFLINE_WALL_S, RECOVERY_S, SKIN_BASELINE_SAMPLES, TelemetryStore,
    create_telemetry_blueprint,
)


# --- evaluate_vitals: manual entry must behave exactly as before ------------

BASELINE = json.loads((ROOT / "tests" / "fixtures" / "evaluate_vitals_baseline.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", sorted(BASELINE))
def test_full_vitals_give_identical_results_to_before(case):
    """Captured from the original evaluate_vitals before it was changed."""
    assert evaluate_vitals(BASELINE[case]["vitals"]) == BASELINE[case]["expected"]


def test_missing_vitals_are_skipped_not_crashed():
    assert evaluate_vitals({}) == {"urgency": "routine", "flags": []}
    assert evaluate_vitals({"heart_rate_bpm": 110})["urgency"] == "act"
    assert evaluate_vitals({"heart_rate_bpm": None, "spo2_pct": 97})["urgency"] == "routine"


@pytest.mark.parametrize("systolic, urgency", [(150, "act"), (85, "act"), (135, "watch"), (95, "watch"), (118, "routine")])
def test_systolic_only_estimate(systolic, urgency):
    result = evaluate_vitals({"bp_systolic": systolic})
    assert result["urgency"] == urgency
    if urgency != "routine":
        assert "estimate" in result["flags"][0]["threshold"]


@pytest.mark.parametrize("sugar, urgency", [(170, "act"), (50, "act"), (130, "watch"), (60, "watch"), (100, "routine")])
def test_blood_sugar_thresholds_match_generate_data(sugar, urgency):
    assert evaluate_vitals({"blood_sugar_mgdl": sugar})["urgency"] == urgency


# --- helpers ---------------------------------------------------------------

class WallClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def readings(device, signal, values, start=1, every=1, activity="rest"):
    return [
        {"device": device, "signal": signal, "t": start + i * every, "value": v, "activity": activity}
        for i, v in enumerate(values)
    ]


def nominal_astroskin(start=1, n=60):
    out = []
    out += readings("astroskin", "heart_rate_bpm", [73] * n, start)
    out += readings("astroskin", "respiratory_rate_bpm", [16] * n, start)
    out += readings("astroskin", "spo2_pct", [97] * n, start)
    out += readings("astroskin", "bp_systolic_est", [118] * n, start)
    out += readings("astroskin", "skin_temp_c", [34.8] * n, start)
    return out


# --- store: rolling window and artifacts ----------------------------------

def test_single_motion_spike_does_not_raise_an_alert():
    store = TelemetryStore()
    hr = [73] * 59 + [180]
    store.ingest(readings("astroskin", "heart_rate_bpm", hr))
    s = store.summary()
    assert s["signals"]["heart_rate_bpm"]["value"] == 73
    assert s["triage"]["urgency"] == "routine"


def test_sustained_high_heart_rate_does_raise_an_alert():
    store = TelemetryStore()
    store.ingest(readings("astroskin", "heart_rate_bpm", [112] * 60))
    flags = store.summary()["triage"]["flags"]
    assert flags[0]["id"] == "heart_rate_bpm" and flags[0]["severity"] == "act"
    assert flags[0]["source"] == "Astroskin smart shirt"


def test_sparse_watch_uses_last_five_readings_not_two():
    """Two Garmin SpO2 readings, one bad: a median of 2 would be 91.5 (act)."""
    store = TelemetryStore()
    spo2 = [97, 97, 97, 96, 86]  # one per minute, last one is a bad-contact artifact
    store.ingest(readings("garmin", "spo2_pct", spo2, start=60, every=60))
    s = store.summary()["signals"]["spo2_pct"]
    assert s["samples"] == 5 and s["value"] == 97


def test_too_few_readings_are_not_judged():
    """Regression: after a loose-strap gap, [152 spike, 70 real] averaged to
    111 bpm and raised 'act'."""
    store = TelemetryStore()
    store.ingest(readings("garmin", "heart_rate_bpm", [152, 70], start=15, every=15))
    s = store.summary()
    assert s["signals"]["heart_rate_bpm"]["status"] == "not_measured"
    assert "need 5" in s["signals"]["heart_rate_bpm"]["reason"]
    assert s["triage"]["urgency"] == "routine"


def test_too_few_from_preferred_device_falls_back_to_one_with_enough():
    store = TelemetryStore()
    batch = readings("garmin", "heart_rate_bpm", [74] * 8, start=0, every=15)
    batch += readings("astroskin", "heart_rate_bpm", [73] * 3, start=103)  # shirt just put on
    store.ingest(batch)
    assert store.summary()["signals"]["heart_rate_bpm"]["source"] == "garmin"


def test_wrist_spo2_alone_cannot_escalate_but_prompts_recheck():
    store = TelemetryStore()
    store.ingest(readings("garmin", "spo2_pct", [86, 88, 86, 97, 88], start=60, every=60))
    flag = store.summary()["triage"]["flags"][0]
    assert flag["severity"] == "watch" and flag["capped_from"] == "act"
    assert "recheck with the oximeter" in flag["threshold"]


def test_astroskin_spo2_still_escalates():
    store = TelemetryStore()
    store.ingest(readings("astroskin", "spo2_pct", [88] * 60))
    flag = store.summary()["triage"]["flags"][0]
    assert flag["severity"] == "act" and "capped_from" not in flag


def test_implausible_values_are_dropped_and_counted():
    store = TelemetryStore()
    out = store.ingest(readings("astroskin", "heart_rate_bpm", [73, 400, 73]))
    assert out["accepted"] == 2 and out["rejected_implausible"] == 1


def test_invalid_batch_stores_nothing():
    store = TelemetryStore()
    good = readings("astroskin", "heart_rate_bpm", [73] * 5)
    bad = good + [{"device": "garmin", "signal": "bp_systolic_est", "t": 6, "value": 120}]
    with pytest.raises(ValueError, match="does not report"):
        store.ingest(bad)
    assert store.summary()["feed"]["accepted"] == 0


@pytest.mark.parametrize("sample, message", [
    ({"device": "fitbit", "signal": "heart_rate_bpm", "t": 1, "value": 70}, "device must be"),
    ({"device": "garmin", "signal": "heart_rate_bpm", "t": -1, "value": 70}, "finite"),
    ({"device": "garmin", "signal": "heart_rate_bpm", "t": 1, "value": float("nan")}, "finite"),
    ({"device": "garmin", "signal": "heart_rate_bpm", "t": 1, "value": True}, "finite"),
    ({"device": "garmin", "signal": "heart_rate_bpm", "t": 1, "value": 70, "activity": "sleep"}, "activity"),
])
def test_bad_samples_are_rejected(sample, message):
    with pytest.raises(ValueError, match=message):
        TelemetryStore().ingest([sample])


# --- store: device coverage -----------------------------------------------

def test_astroskin_is_preferred_when_both_are_worn():
    store = TelemetryStore()
    batch = readings("astroskin", "heart_rate_bpm", [73] * 60)
    batch += readings("garmin", "heart_rate_bpm", [80] * 4, start=15, every=15)
    store.ingest(batch)
    assert store.summary()["signals"]["heart_rate_bpm"]["source"] == "astroskin"


def test_falls_back_to_garmin_once_astroskin_comes_off():
    store = TelemetryStore()
    store.ingest(readings("astroskin", "heart_rate_bpm", [73] * 60))
    # Shirt off at t=60; the watch carries on for another 2 minutes.
    store.ingest(readings("garmin", "heart_rate_bpm", [76] * 9, start=60, every=15))
    s = store.summary()
    assert not s["devices"]["astroskin"]["online"]
    assert s["signals"]["heart_rate_bpm"]["source"] == "garmin"


def test_no_blood_pressure_without_astroskin_is_not_reported_as_normal():
    store = TelemetryStore()
    store.ingest(readings("garmin", "heart_rate_bpm", [73] * 5, every=15))
    s = store.summary()
    assert s["signals"]["bp_systolic_est"]["status"] == "not_measured"
    assert any("Astroskin not being worn" in n for n in s["triage"]["not_evaluated"])


def test_device_online_status():
    store = TelemetryStore()
    store.ingest(nominal_astroskin(n=60) + readings("cgm", "blood_sugar_mgdl", [90], start=60))
    d = store.summary()["devices"]
    assert d["astroskin"]["online"] and d["cgm"]["online"]
    assert not d["garmin"]["online"] and d["garmin"]["last_seen_s_ago"] is None


# --- store: exercise ------------------------------------------------------

def test_exercise_pauses_rate_rules():
    store = TelemetryStore()
    store.ingest(readings("astroskin", "heart_rate_bpm", [140] * 60, activity="exercise"))
    t = store.summary()["triage"]
    assert t["urgency"] == "routine"
    assert any("during exercise" in n for n in t["not_evaluated"])


def test_rules_stay_paused_through_recovery_then_resume():
    store = TelemetryStore()
    store.ingest(readings("astroskin", "heart_rate_bpm", [140] * 60, activity="exercise"))
    # Rested, but heart rate still high just after the session: recovery.
    store.ingest(readings("astroskin", "heart_rate_bpm", [105] * 60, start=61))
    t = store.summary()["triage"]
    assert t["urgency"] == "routine"
    assert any("during recovery" in n for n in t["not_evaluated"])
    # Long after: still 105 at rest is a real finding.
    store.ingest(readings("astroskin", "heart_rate_bpm", [105] * 60, start=61 + RECOVERY_S))
    assert store.summary()["triage"]["urgency"] == "act"


# --- store: skin temperature ----------------------------------------------

def test_skin_temperature_never_uses_the_core_fever_rule():
    """Skin at 35-36 C would be dangerous HYPOthermia as a core reading."""
    store = TelemetryStore()
    store.ingest(readings("astroskin", "skin_temp_c", [34.8] * 60))
    t = store.summary()["triage"]
    assert not any(f["id"] == "body_temp_c" for f in t["flags"])
    assert "thermometer" in t["note"]


def test_skin_temperature_rise_vs_device_baseline_prompts_recheck():
    store = TelemetryStore()
    store.ingest(readings("astroskin", "skin_temp_c", [34.8] * SKIN_BASELINE_SAMPLES, every=10))
    store.ingest(readings("astroskin", "skin_temp_c", [36.0] * 40, start=100, every=10))
    flag = next(f for f in store.summary()["triage"]["flags"] if f["id"] == "skin_temp_c")
    assert flag["severity"] == "watch" and "thermometer" in flag["threshold"]


def test_skin_baseline_is_per_device():
    """Wrist runs ~1 C cooler than forehead: that gap is not a temperature drop."""
    store = TelemetryStore()
    store.ingest(readings("garmin", "skin_temp_c", [33.6] * 8, every=120))
    s = store.summary()["signals"]["skin_temp_c"]
    assert s["baseline"] == 33.6 and s["change_vs_baseline"] == 0.0


def test_skin_baseline_still_learning_is_reported():
    # Enough to judge (3) but fewer than the 6 needed to set a baseline.
    store = TelemetryStore()
    store.ingest(readings("garmin", "skin_temp_c", [33.6] * 4, every=120))
    assert any("still learning" in n for n in store.summary()["triage"]["not_evaluated"])


# --- store: feed health, restarts, callback -------------------------------

def test_feed_goes_offline_when_simulator_stops():
    clock = WallClock()
    store = TelemetryStore(clock=clock)
    store.ingest(readings("astroskin", "heart_rate_bpm", [73] * 5))
    assert store.summary()["feed"]["online"]
    clock.t += FEED_OFFLINE_WALL_S + 1
    assert not store.summary()["feed"]["online"]


def test_simulator_restart_clears_old_readings():
    store = TelemetryStore()
    store.ingest(readings("astroskin", "heart_rate_bpm", [120] * 60, start=5000))
    store.ingest(readings("astroskin", "heart_rate_bpm", [73] * 60, start=1))
    s = store.summary()
    assert s["sim_time_s"] == 60
    assert s["signals"]["heart_rate_bpm"]["value"] == 73


def test_change_callback_fires_on_changes_only():
    calls = []
    store = TelemetryStore(on_urgency_change=lambda old, new, detail: calls.append((old, new, detail)))
    store.ingest(readings("astroskin", "heart_rate_bpm", [73] * 60))         # start -> routine: silent
    store.ingest(readings("astroskin", "heart_rate_bpm", [73] * 10, start=61))  # unchanged: silent
    assert calls == []
    store.ingest(readings("astroskin", "heart_rate_bpm", [115] * 60, start=71))  # routine -> act
    assert len(calls) == 1 and calls[0][:2] == ("routine", "act")
    assert "Heart rate 115 bpm" in calls[0][2]


# --- HTTP endpoints -------------------------------------------------------

@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(create_telemetry_blueprint())
    return app.test_client()


def test_endpoints_round_trip(client):
    r = client.post("/api/telemetry", json={"samples": nominal_astroskin()})
    assert r.status_code == 200 and r.get_json()["urgency"] == "routine"
    latest = client.get("/api/telemetry/latest").get_json()
    assert latest["signals"]["heart_rate_bpm"]["value"] == 73
    series = client.get("/api/telemetry/series?signal=heart_rate_bpm&seconds=30").get_json()
    assert len(series["points"]) == 31 and series["points"][-1]["device"] == "astroskin"
    assert client.post("/api/telemetry/reset").status_code == 200
    assert client.get("/api/telemetry/latest").get_json()["sim_time_s"] is None


@pytest.mark.parametrize("path, body", [
    ("/api/telemetry", None),
    ("/api/telemetry", {"samples": "nope"}),
    ("/api/telemetry", {"samples": [{"device": "garmin"}]}),
])
def test_bad_ingest_requests_get_400(client, path, body):
    r = client.post(path, json=body) if body is not None else client.post(path, data="x")
    assert r.status_code == 400 and "error" in r.get_json()


@pytest.mark.parametrize("query", ["signal=pulse", "signal=spo2_pct&device=fitbit", "signal=spo2_pct&seconds=0", "signal=spo2_pct&seconds=abc"])
def test_bad_series_requests_get_400(client, query):
    assert client.get(f"/api/telemetry/series?{query}").status_code == 400


# --- simulator ------------------------------------------------------------

def count_by_device(samples):
    out = {}
    for s in samples:
        out[s["device"]] = out.get(s["device"], 0) + 1
    return out


def test_garmin_sends_far_fewer_readings_than_astroskin():
    sim = Simulator(seed=1, astroskin="always")
    samples = sim.advance(600)  # t 1-600: 10 sim minutes, resting (exercise starts at 900)
    counts = count_by_device(samples)
    assert counts["garmin"] * 10 < counts["astroskin"]
    hr = [s for s in samples if s["signal"] == "heart_rate_bpm"]
    assert sum(s["device"] == "astroskin" for s in hr) > 10 * sum(s["device"] == "garmin" for s in hr)


def test_garmin_samples_heart_rate_faster_while_exercising():
    def garmin_hr(sim, seconds):
        return sum(1 for s in sim.advance(seconds) if s["device"] == "garmin" and s["signal"] == "heart_rate_bpm")
    sim = Simulator(seed=2, astroskin="never")
    rest = garmin_hr(sim, 300)  # t 1-300, resting
    sim.advance(600)            # to t=900, exercise starts
    exercise = garmin_hr(sim, 300)
    assert exercise >= 2.5 * rest


def test_astroskin_schedule():
    sim = Simulator(seed=3, astroskin="schedule")
    assert sim.astroskin_worn(100)             # start-of-run monitoring block
    assert not sim.astroskin_worn(700)         # quiet period: watch only
    assert sim.astroskin_worn(950)             # exercise
    sim.set_scenario("acute_febrile")
    assert sim.astroskin_worn(700)             # crew puts it on when unwell


def test_astroskin_never_and_always():
    assert "astroskin" not in count_by_device(Simulator(seed=4, astroskin="never").advance(300))
    assert count_by_device(Simulator(seed=4, astroskin="always").advance(300))["astroskin"] > 300


def test_sparse_watch_reading_stays_paused_until_window_is_clean():
    """Regression: Garmin breathing rate (1/min) uses its last 5 readings, which
    reached back into the workout once the 3-minute recovery clock ran out."""
    store = TelemetryStore()
    store.ingest(readings("garmin", "respiratory_rate_bpm", [26] * 5, start=60, every=60, activity="exercise"))
    # Rested readings start right after: t=360..
    store.ingest(readings("garmin", "respiratory_rate_bpm", [16] * 4, start=360, every=60))
    # Clock is now >180 s past the last exercise reading (t=300), but the
    # 5-reading window still holds one from the workout: must stay paused.
    t = store.summary()["triage"]
    assert t["urgency"] == "routine"
    assert any("during recovery" in n for n in t["not_evaluated"])


def test_unwell_crewmember_does_not_exercise():
    sim = Simulator(seed=8)
    assert sim.exercising(950)
    sim.set_scenario("acute_febrile")
    assert not sim.exercising(950)


def test_unknown_scenario_keeps_current_one():
    sim = Simulator(seed=5)
    sim.set_scenario("acute_febrile")
    assert not sim.set_scenario("manual_entry")
    assert sim.scenario == "acute_febrile"


def test_simulator_is_deterministic_for_a_seed():
    assert Simulator(seed=9).advance(300) == Simulator(seed=9).advance(300)


def test_every_simulated_reading_is_valid_for_the_store():
    sim = Simulator(seed=6, astroskin="always")
    TelemetryStore().ingest(sim.advance(1800))  # raises on any invalid sample


def stream(sim, store, seconds, chunk=10):
    urgencies = []
    for _ in range(seconds // chunk):
        urgencies.append(store.ingest(sim.advance(chunk))["urgency"])
    return urgencies


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_a_nominal_hour_raises_no_false_alarms(seed):
    """An hour of nominal data, including an exercise session, Garmin motion
    spikes, bad SpO2 readings and loose-strap gaps: no alert, ever."""
    sim = Simulator(seed=seed, astroskin="schedule")
    store = TelemetryStore()
    urgencies = stream(sim, store, 3600)
    assert set(urgencies) == {"routine"}, store.summary()["triage"]


def test_febrile_scenario_drifts_into_an_alert():
    sim = Simulator(seed=21, astroskin="schedule")
    store = TelemetryStore()
    stream(sim, store, 300)
    sim.set_scenario("acute_febrile")  # HR 104, SpO2 94, RR 22, core 38.4
    urgencies = stream(sim, store, 1200)
    assert urgencies[0] == "routine"   # not instant: it drifts
    assert urgencies[-1] == "act"
    ids = {f["id"] for f in store.summary()["triage"]["flags"]}
    assert {"heart_rate_bpm", "spo2_pct", "respiratory_rate_bpm"} <= ids


def test_scenario_targets_cover_every_simulated_vital():
    needed = {"heart_rate_bpm", "respiratory_rate_bpm", "spo2_pct", "bp_systolic", "body_temp_c"}
    for name, spec in SCENARIOS.items():
        assert needed <= set(spec["vitals"]), name


def test_cadence_matches_device_signal_lists():
    from telemetry import DEVICES
    for device, signals in CADENCE.items():
        assert set(signals) == set(DEVICES[device]["signals"]), device
