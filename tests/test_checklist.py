"""Tests for the emergency checklist lookup and the split checklist PDFs."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from checklist import (  # noqa: E402
    MANIFEST_FILE, PDF_DIR, ChecklistTriage, create_checklist_blueprint, load_json,
)
from clinical import SYMPTOM_IDS  # noqa: E402

if not MANIFEST_FILE.is_file():
    pytest.skip("run scripts/split_checklist.py first", allow_module_level=True)

ENGINE = ChecklistTriage()
INDEX = load_json(ROOT / "data" / "checklist_sections.json")["sections"]
MARS_ONE_WAY_S = 1120


def run(symptoms=(), signs=(), delay=MARS_ONE_WAY_S, **vitals):
    """vitals as name=value, or name=(value, context)."""
    v = {}
    for k, val in vitals.items():
        value, ctx = val if isinstance(val, tuple) else (val, "rest")
        v[k] = {"value": value, "source": "test sensor", "context": ctx}
    return ENGINE.evaluate(list(symptoms), list(signs), v, delay)


def ids(result):
    return [m["id"] for m in result["matches"]]


def match(result, sid):
    return next(m for m in result["matches"] if m["id"] == sid)


# --- integrity of the drafted decision tree ---------------------------------

def test_every_rule_points_at_real_procedures_and_inputs():
    for rule in ENGINE.rules:
        for sid in rule["sections"]:
            assert sid in ENGINE.sections, f"{rule['id']} -> unknown section {sid}"
        for key in ("symptoms_any", "symptoms_all"):
            for s in rule.get(key, []):
                assert s in SYMPTOM_IDS, f"{rule['id']} uses unknown symptom {s}"
        for key in ("signs_any", "signs_all", "signs_none"):
            for s in rule.get(key, []):
                assert s in ENGINE.signs, f"{rule['id']} uses unknown sign {s}"
        assert rule["tier"] in ("emergency", "urgent", "routine"), rule["id"]
        assert rule.get("source"), f"{rule['id']} has no source"


def test_rule_ids_are_unique():
    rule_ids = [r["id"] for r in ENGINE.rules]
    assert len(rule_ids) == len(set(rule_ids))


def test_every_emergency_procedure_is_reachable():
    """Section 1 of the checklist must be reachable directly or as 'related'."""
    direct = {sid for r in ENGINE.rules for sid in r["sections"]}
    related = {rel for sid in direct for rel in ENGINE.sections[sid]["related"]}
    emergency = {s["id"] for s in INDEX if s["group"].startswith("1 ")}
    assert emergency - direct - related == set()


def test_every_sign_is_used_by_a_rule():
    used = {s for r in ENGINE.rules for k in ("signs_any", "signs_all") for s in r.get(k, [])}
    assert set(ENGINE.signs) <= used


def test_every_reported_symptom_leads_somewhere():
    """Each clinical.py symptom either opens a procedure or has a not_covered note."""
    for s in SYMPTOM_IDS:
        result = run(symptoms=[s])
        assert result["matches"] or any(n["input"] == s for n in result["not_covered"]), s


# --- split PDFs -------------------------------------------------------------

def test_every_section_was_split_and_verified():
    assert len(ENGINE.sections) == len(INDEX)
    for sid, s in ENGINE.sections.items():
        assert s["heading_verified"], sid
        pdf = PDF_DIR / s["file"]
        assert pdf.is_file() and pdf.read_bytes()[:4] == b"%PDF", sid


def test_known_surgeon_approval_medications_are_highlighted():
    def meds(sid):
        return {m["name"] for m in ENGINE.sections[sid]["surgeon_approval_meds"]}
    assert "Proventil" in meds("breathing_difficulty")
    assert "Valium" in meds("seizures")
    assert {"Epinephrine", "Benadryl", "Dexamethasone"} <= meds("allergic_severe")


def test_unstarred_medications_are_not_flagged():
    """Chest Pain gives Ascriptin (aspirin) - not starred in the checklist."""
    assert ENGINE.sections["chest_pain"]["surgeon_approval_meds"] == []


def test_split_pdf_page_counts_match_ranges():
    from pypdf import PdfReader
    for sid in ("cpr", "chest_pain", "dcs", "breathing_difficulty"):
        s = ENGINE.sections[sid]
        assert len(PdfReader(str(PDF_DIR / s["file"])).pages) == len(s["pdf_pages"]), sid


# --- crewmate-observed emergencies -----------------------------------------

def test_not_breathing_opens_cpr_first():
    r = run(signs=["not_breathing"])
    assert ids(r)[0] == "cpr"
    assert r["tier"] == "emergency"
    assert r["headline"].startswith("EMERGENCY: open CPR")


def test_choking_conscious_vs_unconscious():
    assert ids(run(signs=["choking"]))[0] == "choking_conscious"
    r = run(signs=["choking", "unresponsive"])
    assert ids(r)[0] == "choking_unconscious"
    assert "choking_conscious" not in ids(r)


def test_unconscious_choking_starts_with_airway_then_cpr():
    """Regression: the generic 'unresponsive' rule put CPR ahead of the
    unconscious-choking procedure, which itself sends you to CPR at step 8."""
    assert ids(run(signs=["choking", "not_breathing"]))[:2] == ["choking_unconscious", "cpr"]
    assert ids(run(signs=["choking", "unresponsive"]))[:2] == ["choking_unconscious", "cpr"]


def test_heavy_bleeding_controls_bleeding_before_shock():
    assert ids(run(signs=["heavy_bleeding"]))[:2] == ["lacerations_major", "shock"]


def test_after_eva_with_chest_pain_is_serious_dcs():
    r = run(symptoms=["chest_pain"], signs=["symptoms_after_eva"])
    assert match(r, "dcs")["tier"] == "emergency"


# --- reported symptoms -------------------------------------------------------

def test_chest_pain_is_an_emergency_with_ecg():
    r = run(symptoms=["chest_pain"])
    assert ids(r)[:2] == ["chest_pain", "cardiac_monitoring"]


def test_chest_pain_with_breathlessness_leads_with_chest_pain():
    """Regression: the weaker 'short of breath' rule (urgent) made Breathing
    Difficulty 'primary' and pushed it above Chest Pain."""
    assert ids(run(symptoms=["chest_pain", "shortness_breath"]))[0] == "chest_pain"


def test_exercise_vitals_are_not_reported_as_uncovered():
    """Regression: HR 118 / RR 25 mid-workout appeared under 'not covered'."""
    r = run(heart_rate_bpm=(118, "exercise"), respiratory_rate_bpm=(25, "recovery"))
    assert r["not_covered"] == []
    r = run(heart_rate_bpm=118)
    assert any(n["input"].startswith("Heart rate 118") for n in r["not_covered"])


def test_rash_plus_breathlessness_is_treated_as_severe_allergy():
    r = run(symptoms=["skin_rash_burn", "shortness_breath"])
    assert match(r, "allergic_severe")["tier"] == "emergency"


def test_joint_pain_with_tingling_points_to_dcs():
    assert "dcs" in ids(run(symptoms=["joint_pain", "tingling"]))


def test_emergencies_come_before_routine_items():
    r = run(symptoms=["chest_pain", "cough"])
    tiers = [m["tier"] for m in r["matches"]]
    assert tiers == sorted(tiers, key=["emergency", "urgent", "routine"].index)
    assert ids(r)[0] == "chest_pain" and ids(r)[-1] == "bronchitis"


# --- live vitals -------------------------------------------------------------

def test_low_spo2_alone_opens_breathing_and_oxygen():
    r = run(spo2_pct=88)
    assert ids(r)[:2] == ["breathing_difficulty", "oxygen_supplemental"]
    assert "SpO₂ 88% from test sensor" in match(r, "breathing_difficulty")["reasons"][0]


def test_low_bp_with_fast_pulse_is_shock():
    r = run(bp_systolic=84, heart_rate_bpm=125)
    assert match(r, "shock")["tier"] == "emergency"


def test_low_bp_alone_is_urgent():
    assert match(run(bp_systolic=84), "shock")["tier"] == "urgent"


def test_fast_heart_rate_during_exercise_is_ignored():
    assert "cardiac_monitoring" not in ids(run(heart_rate_bpm=(145, "exercise")))
    assert "cardiac_monitoring" in ids(run(heart_rate_bpm=145))


def test_thermometer_fever_counts_as_reported_fever():
    r = run(symptoms=["cough"], body_temp_c=38.6)
    assert match(r, "bronchitis")["tier"] == "urgent"
    assert r["inputs"]["derived"] == ["fever_chills (temperature)"]


# --- never a blank screen ----------------------------------------------------

def test_inputs_without_a_procedure_are_explained():
    r = run(symptoms=["tingling"])
    assert r["matches"] == []
    assert r["not_covered"][0]["input"] == "tingling"
    assert "No checklist procedure" in r["headline"]


def test_fever_alone_says_to_use_the_thermometer():
    r = run(body_temp_c=38.6)
    assert any("thermometer" in n["note"] for n in r["not_covered"])


def test_abnormal_blood_sugar_is_reported_not_dropped():
    r = run(blood_sugar_mgdl=190)
    assert any(n["input"].startswith("Blood sugar 190") for n in r["not_covered"])


# --- comms delay and output shape -------------------------------------------

def test_comms_note_states_the_round_trip():
    r = run(signs=["seizing"])
    assert r["comms"]["round_trip_min"] == 37
    assert "37 min" in r["comms"]["note"]


def test_related_procedures_exclude_ones_already_shown():
    r = run(signs=["not_breathing"])
    for m in r["matches"]:
        assert not {x["id"] for x in m["related"]} & set(ids(r))


def test_each_match_carries_its_pdf_and_page_citation():
    m = run(symptoms=["chest_pain"])["matches"][0]
    assert m["pdf_url"] == "/api/checklist/pdf/chest_pain"
    assert m["pages"] == "1-36 to 1-38"
    assert m["sources"]


# --- HTTP --------------------------------------------------------------------

def live_summary_with(spo2=None, online=True):
    signals = {}
    if spo2 is not None:
        signals["spo2_pct"] = {"status": "measured", "value": spo2, "source_label": "Astroskin smart shirt",
                               "context": "rest", "samples": 60}
    return {"feed": {"online": online}, "signals": signals}


def make_client(summary=None, triage=None):
    app = Flask(__name__)
    app.register_blueprint(create_checklist_blueprint(
        live_summary=(lambda: summary) if summary is not None else None,
        comms_delay=lambda: MARS_ONE_WAY_S, triage=triage))
    return app.test_client()


def test_inputs_endpoint_lists_symptoms_and_signs():
    data = make_client().get("/api/checklist/inputs").get_json()
    assert len(data["symptoms"]) == len(SYMPTOM_IDS)
    assert {s["id"] for s in data["signs"]} == set(ENGINE.signs)


def test_sections_and_pdf_endpoints():
    client = make_client()
    assert len(client.get("/api/checklist/sections").get_json()) == len(INDEX)
    r = client.get("/api/checklist/pdf/cpr")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:4] == b"%PDF"
    assert client.get("/api/checklist/pdf/not_a_section").status_code == 404


def test_match_uses_live_wearable_vitals():
    client = make_client(summary=live_summary_with(spo2=86))
    r = client.post("/api/checklist/match", json={"symptoms": [], "signs": []}).get_json()
    assert r["tier"] == "emergency"
    assert r["vitals_used"]["spo2_pct"]["source"] == "Astroskin smart shirt"


def test_offline_feed_is_not_used():
    client = make_client(summary=live_summary_with(spo2=86, online=False))
    r = client.post("/api/checklist/match", json={}).get_json()
    assert r["matches"] == []


def test_entered_vitals_override_live_ones():
    client = make_client(summary=live_summary_with(spo2=86))
    r = client.post("/api/checklist/match", json={"vitals": {"spo2_pct": 98}}).get_json()
    assert r["matches"] == []
    assert r["vitals_used"]["spo2_pct"]["source"] == "entered by crew"


@pytest.mark.parametrize("body, message", [
    ({"symptoms": ["broken_leg"]}, "symptoms must be"),
    ({"signs": ["on_fire"]}, "signs must be"),
    ({"vitals": {"pulse": 80}}, "unknown vital"),
    ({"vitals": {"spo2_pct": 120}}, "spo2_pct must be"),
    ({"vitals": {"spo2_pct": True}}, "spo2_pct must be"),
])
def test_bad_match_requests_get_400(body, message):
    r = make_client().post("/api/checklist/match", json=body)
    assert r.status_code == 400 and message in r.get_json()["error"]


def test_missing_pdfs_give_503_with_instructions():
    client = make_client(triage=ChecklistTriage(manifest=None))
    client_engine = client.application.blueprints["checklist"].engine
    client_engine.manifest = None
    r = client.post("/api/checklist/match", json={})
    assert r.status_code == 503 and "split_checklist.py" in r.get_json()["error"]
