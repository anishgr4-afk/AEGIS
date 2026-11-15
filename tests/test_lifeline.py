"""Lifeline page wiring: every checklist procedure can be reached from crew
input, no input asks for a reading the wearables already give, and the page's
endpoints respond."""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from checklist import MANIFEST_FILE, ChecklistTriage, load_json  # noqa: E402

if not MANIFEST_FILE.is_file():
    pytest.skip("run scripts/split_checklist.py first", allow_module_level=True)

ENGINE = ChecklistTriage()
INDEX = load_json(ROOT / "data" / "checklist_sections.json")["sections"]

# Pages only reached from inside another procedure (technique, reference),
# or not about a patient at all. Not offered as a crew input on purpose.
NOT_CREW_INPUTS = {
    "intubation", "cricothyrotomy",                  # from CPR / choking steps
    "iv_injections", "im_injections", "injector_technique",
    "medication_side_effects", "tox_levels",         # reference pages
    "radiation",                                      # dosimeter log, not symptoms
}


def run(symptoms=(), signs=()):
    return [m["id"] for m in ENGINE.evaluate(list(symptoms), list(signs), {}, 0)["matches"]]


def test_every_procedure_is_reachable_from_crew_input():
    direct = {sid for r in ENGINE.rules for sid in r["sections"]}
    related = {rel for sid in direct for rel in ENGINE.sections[sid]["related"]}
    missing = {s["id"] for s in INDEX} - direct - related - NOT_CREW_INPUTS
    assert missing == set()


def test_no_input_asks_for_a_live_measured_reading():
    live_words = re.compile(r"heart ?rate|pulse rate|blood pressure|breathing rate|spo2|oxygen sat|blood sugar|glucose", re.I)
    for sid, sign in ENGINE.signs.items():
        assert not live_words.search(sign["label"] + " " + sign.get("prompt", "")), sid


@pytest.mark.parametrize("sign, section", [
    ("no_pulse", "cpr"),
    ("pale_sweaty", "shock"),
    ("hives_itching", "allergic_mild"),
    ("grit_in_eye", "eye_foreign_body"),
    ("red_sore_eye", "eye_exam"),
    ("cabin_too_hot", "hot_cabin"),
    ("back_pain", "back_pain"),
    ("toothache", "toothache"),
    ("heartburn", "indigestion"),
    ("cannot_urinate", "urinary_retention"),
    ("cant_sleep", "insomnia"),
])
def test_new_inputs_open_their_procedure(sign, section):
    assert section in run(signs=[sign])


def test_no_pulse_is_an_emergency_that_opens_cpr_first():
    assert run(signs=["no_pulse"])[0] == "cpr"


def test_mild_allergy_steps_aside_for_swelling():
    found = run(signs=["hives_itching", "allergic_swelling"])
    assert "allergic_severe" in found and "allergic_mild" not in found


def test_symptoms_after_depressurisation_point_to_altitude_sickness():
    assert "altitude_sickness" in run(symptoms=["headache_dizziness"], signs=["symptoms_after_eva"])
    assert "altitude_sickness" not in run(symptoms=["headache_dizziness"])


@pytest.fixture()
def client(monkeypatch):
    app_module = importlib.import_module("app")
    monkeypatch.setattr(app_module, "persist", lambda: None)
    return app_module.app.test_client()


def test_lifeline_page_is_served(client):
    resp = client.get("/lifeline")
    assert resp.status_code == 200 and b"AEGIS" in resp.data


def test_lifeline_live_bundles_every_crewmember_and_comms(client):
    body = client.get("/api/lifeline/live").get_json()
    assert {"crew", "default_crew", "comms", "mission"} <= set(body)
    assert [c["id"] for c in body["crew"]] == ["CDR", "PLT", "MS1", "MS2"]
    for member in body["crew"]:
        assert "signals" in member["telemetry"] and "triage" in member["telemetry"]
    assert body["comms"]["min_round_trip_seconds"] == pytest.approx(2 * body["comms"]["comms_delay_seconds"])


def test_page_has_no_hard_coded_vitals():
    page = (ROOT / "Lifeline_Emergency.html").read_text(encoding="utf-8")
    assert "var CREW=[];" in page and "v:[[" not in page
