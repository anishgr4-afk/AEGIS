"""Live telemetry for several crewmembers at once."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from checklist import MANIFEST_FILE, create_checklist_blueprint  # noqa: E402
from simulator import Simulator, default_crew_scenarios, parse_crew  # noqa: E402
from telemetry import create_telemetry_blueprint, load_crew  # noqa: E402

CREW, DEFAULT = load_crew()
IDS = [c["id"] for c in CREW]


def make_app():
    app = Flask(__name__)
    bp = create_telemetry_blueprint()
    app.register_blueprint(bp)
    if MANIFEST_FILE.is_file():
        app.register_blueprint(create_checklist_blueprint(live_summary=bp.crew.summary))
    return app.test_client(), bp


def feed(client, scenarios: dict[str, str], seconds=900, step=30):
    sims = {cid: Simulator(seed=i, astroskin="always", scenario=sc) for i, (cid, sc) in enumerate(scenarios.items())}
    for _ in range(seconds // step):
        batch = [{**s, "crew": cid} for cid, sim in sims.items() for s in sim.advance(step)]
        r = client.post("/api/telemetry", json={"samples": batch})
        assert r.status_code == 200, r.get_json()
    return r.get_json()


def test_roster_has_four_crew_and_a_valid_default():
    assert IDS == ["CDR", "PLT", "MS1", "MS2"] and DEFAULT in IDS


def test_one_crewmembers_shock_does_not_leak_into_anothers_triage():
    client, _ = make_app()
    last = feed(client, {"MS2": "hypovolemic_shock", "CDR": "nominal_transit"})
    assert last["crew"] == {"MS2": "act", "CDR": "routine"}
    assert client.get("/api/telemetry/latest?crew=MS2").get_json()["triage"]["urgency"] == "act"
    assert client.get("/api/telemetry/latest?crew=CDR").get_json()["triage"]["urgency"] == "routine"
    # Nobody posted for PLT: nothing measured, never "normal".
    plt = client.get("/api/telemetry/latest?crew=PLT").get_json()
    assert plt["feed"]["online"] is False
    assert all(s["status"] == "not_measured" for s in plt["signals"].values())


def test_crew_endpoint_lists_everyone():
    client, _ = make_app()
    body = client.get("/api/telemetry/crew").get_json()
    assert [c["id"] for c in body["crew"]] == IDS and body["default"] == DEFAULT


def test_readings_without_crew_go_to_the_default_wearer():
    client, bp = make_app()
    sim = Simulator(seed=1, astroskin="always")
    client.post("/api/telemetry", json={"samples": sim.advance(60)})
    assert bp.crew.summary(DEFAULT)["feed"]["accepted"] > 0
    assert bp.store is bp.crew.store(DEFAULT)


def test_unknown_crew_is_rejected_and_nothing_is_stored():
    client, bp = make_app()
    good = {**Simulator(seed=1).advance(30)[0], "crew": "CDR"}
    bad = {**good, "crew": "XYZ"}
    assert client.post("/api/telemetry", json={"samples": [good, bad]}).status_code == 400
    assert bp.crew.summary("CDR")["feed"]["accepted"] == 0
    assert client.get("/api/telemetry/latest?crew=XYZ").status_code == 400


def test_reset_clears_every_crewmember():
    client, bp = make_app()
    feed(client, {"MS1": "nominal_transit", "PLT": "nominal_transit"}, seconds=120)
    client.post("/api/telemetry/reset")
    assert all(bp.crew.summary(cid)["sim_time_s"] is None for cid in IDS)


@pytest.mark.skipif(not MANIFEST_FILE.is_file(), reason="run scripts/split_checklist.py first")
def test_checklist_match_uses_the_named_crewmembers_vitals():
    client, _ = make_app()
    feed(client, {"MS2": "hypovolemic_shock", "CDR": "nominal_transit"})
    ms2 = client.post("/api/checklist/match", json={"crew": "MS2"}).get_json()
    cdr = client.post("/api/checklist/match", json={"crew": "CDR"}).get_json()
    assert "shock" in [m["id"] for m in ms2["matches"]]
    assert cdr["matches"] == []
    assert client.post("/api/checklist/match", json={"crew": "XYZ"}).status_code == 400


def test_simulator_default_is_one_unwell_and_the_rest_nominal():
    scenarios = default_crew_scenarios()
    assert list(scenarios) == IDS
    unwell = [cid for cid, sc in scenarios.items() if sc != "nominal_transit"]
    assert unwell == [DEFAULT]


def test_simulator_crew_option_sets_scenarios_per_person():
    roster = default_crew_scenarios()
    out = parse_crew("PLT=post_eva_dcs,MS1=acute_febrile", roster)
    assert out == {"CDR": "nominal_transit", "PLT": "post_eva_dcs", "MS1": "acute_febrile", "MS2": "nominal_transit"}
    with pytest.raises(ValueError):
        parse_crew("XYZ=nominal_transit", roster)
    with pytest.raises(ValueError):
        parse_crew("PLT=not_a_scenario", roster)


def test_exercise_slots_are_staggered_between_crew():
    a, b = Simulator(seed=1), Simulator(seed=1, schedule_offset=480)
    slots_a = {t for t in range(1800) if a.exercising(t)}
    slots_b = {t for t in range(1800) if b.exercising(t)}
    assert slots_a and slots_b and slots_a != slots_b
