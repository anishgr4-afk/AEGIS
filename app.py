"""Offline Flask API for the ISS / Orion Crew Health Console demo.

Thresholds are configurable demo rules from the supplied project brief. This
software is not a medical device and must not be used for operational care.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from flask import Flask, jsonify, make_response, request, send_from_directory
from flask_cors import CORS

from clinical import (
    SCENARIOS,
    SYMPTOM_IDS,
    add_audit_event,
    default_clinical_state,
    evaluate_vitals,
    local_pdf_sources,
    validate_symptoms,
    validate_vitals,
)
from generate_data import generate_history
from telemetry import create_telemetry_blueprint
from checklist import create_checklist_blueprint
from comms import DEFAULT_DISTANCE_KM, MAX_DISTANCE_KM, comms_fields

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "mission_data.json"
CLINICAL_FILE = ROOT / "data" / "clinical_state.json"
PROTOCOL_FILE = ROOT / "data" / "medical_database.json"
app = Flask(__name__)
CORS(app)
lock = RLock()
history: list[dict[str, Any]] = []
clinical_state: dict[str, Any] = {}
active_environment = "MARS_TRANSIT"
# Current Earth distance. Comms delay is derived from it (see comms.py).
active_distance_km = DEFAULT_DISTANCE_KM[active_environment]
action_log: list[dict[str, str]] = []
ENVIRONMENTS = {
    "ISS_LEO": {"label": "ISS / Low Earth Orbit", "rate": 0.5},
    "MARS_TRANSIT": {"label": "Mars Transit (Deep Space)", "rate": 1.3},
    "LUNAR_SURFACE": {"label": "Lunar Surface", "rate": 0.9},
    "MARS_HABITAT": {"label": "Mars Habitat", "rate": 0.9},
}


def load_history() -> list[dict[str, Any]]:
    if not DATA_FILE.exists():
        # Use the same standalone generator invoked by operators, then load
        # its result to keep startup behavior explicit and reproducible.
        subprocess.run([sys.executable, str(ROOT / "generate_data.py")], check=True, cwd=ROOT)
    with DATA_FILE.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, list) or len(data) != 115:
        raise ValueError("mission_data.json must contain exactly 115 day records")
    return data


def finite_number(payload: dict, key: str, low: float, high: float, integer: bool = False):
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{key} must be a number from {low} to {high}")
    return int(value) if integer else float(value)


def status_for_vitals(values: dict, short_sleep_streak: int) -> dict[str, str]:
    hr, sys, dia = values["heart_rate_bpm"], values["bp_systolic"], values["bp_diastolic"]
    temp, sugar, spo2 = values["body_temp_c"], values["blood_sugar_mgdl"], values["spo2_pct"]
    sleep, mood = values["sleep_hours"], values["mood_score"]
    return {
        "heart_rate_bpm": "red" if hr > 100 or hr < 48 else "amber" if hr > 88 or hr < 55 else "green",
        "blood_pressure": "red" if sys > 145 or dia > 92 or sys < 90 or dia < 55 else "amber" if sys > 130 or dia > 84 or sys < 100 or dia < 65 else "green",
        "body_temp_c": "red" if temp > 38 or temp < 35.5 else "amber" if temp >= 37.5 or temp < 36.1 else "green",
        "blood_sugar_mgdl": "red" if sugar > 160 or sugar < 54 else "amber" if sugar > 125 or sugar < 65 else "green",
        "spo2_pct": "red" if spo2 < 92 else "amber" if spo2 < 95 else "green",
        "sleep_hours": "red" if sleep < 5 or short_sleep_streak >= 3 else "amber" if sleep < 6.5 else "green",
        "mood_score": "red" if mood <= 4 else "amber" if mood <= 6 else "green",
    }


def color(value: float, green: float, amber: float) -> str:
    return "green" if value >= green else "amber" if value >= amber else "red"


def update_systems(day: dict) -> None:
    v, m = day["vitals"], day["space_metrics"]
    systolic, diastolic = v["blood_pressure"]["systolic"], v["blood_pressure"]["diastolic"]
    cardiovascular = "red" if "red" in (v["heart_rate_bpm"]["status"], v["blood_pressure"]["status"]) else "amber" if "amber" in (v["heart_rate_bpm"]["status"], v["blood_pressure"]["status"]) else "green"
    musculoskeletal = "red" if "red" in (m["bone_density_pct"]["status"], m["muscle_strength_pct"]["status"], m["vo2_max"]["status"]) else "amber" if "amber" in (m["bone_density_pct"]["status"], m["muscle_strength_pct"]["status"], m["vo2_max"]["status"]) else "green"
    keys = ("sleep_hours", "mood_score")
    neuro = "red" if any(v[k]["status"] == "red" for k in keys) else "amber" if any(v[k]["status"] == "amber" for k in keys) else "green"
    keys = ("body_temp_c", "blood_sugar_mgdl", "spo2_pct")
    metabolic = "red" if any(v[k]["status"] == "red" for k in keys) else "amber" if any(v[k]["status"] == "amber" for k in keys) else "green"
    day["body_systems"] = {
        "cardiovascular": cardiovascular, "musculoskeletal": musculoskeletal,
        "neuro_circadian": neuro, "metabolic_immune": metabolic,
        "radiation_cellular": m["radiation_msv"]["status"],
    }


def make_alerts(day: dict) -> list[dict]:
    alerts = []
    systems = day["body_systems"]
    for domain, status in systems.items():
        if status == "green":
            continue
        domain_title = domain.replace("_", " ").title()
        if domain == "radiation_cellular":
            action = "CRITICAL: Relocate to storm shelter and review dosimetry." if status == "red" else "Restrict non-essential EVA and review ALARA shielding allocation."
        elif domain == "musculoskeletal":
            action = "Coordinate clinical review and resume supervised resistive exercise as tolerated."
        elif domain == "neuro_circadian":
            action = "Protect sleep opportunity, review workload, and use crew support resources."
        else:
            action = "Recheck the measurement and consult the flight surgeon if persistent or worsening."
        alerts.append({"severity": status, "domain": domain_title, "title": f"{domain_title} {status.title()}", "explanation": "One or more simulated measurements crossed the console's configured threshold.", "action": action, "nasa_ref": "Project demo thresholds"})
    if not alerts:
        alerts.append({"severity": "green", "domain": "All Systems", "title": "Nominal Deep-Space Transit", "explanation": "Simulated values are within configured console thresholds.", "action": "Continue planned daily countermeasures and monitoring.", "nasa_ref": "Project demo thresholds"})
    return alerts


def persist() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = DATA_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(DATA_FILE)


def record_action(message: str) -> None:
    action_log.append({"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "action": message})
    del action_log[:-30]


def active_day() -> dict:
    return history[-1]


def apply_inputs(payload: dict) -> dict:
    required = {
        "exercise_mins": (0, 240, True), "sleep_hours": (0, 16, False),
        "heart_rate_bpm": (25, 220, True), "bp_systolic": (50, 250, True),
        "bp_diastolic": (25, 150, True), "body_temp_c": (30, 43, False),
        "blood_sugar_mgdl": (20, 600, True), "spo2_pct": (50, 100, True), "mood_score": (1, 10, True),
    }
    values = {key: finite_number(payload, key, *bounds) for key, bounds in required.items()}
    day = active_day()
    prior = [entry["vitals"]["sleep_hours"]["value"] for entry in history[-3:-1]]
    streak = 1 + sum(1 for value in reversed(prior) if value < 6)
    statuses = status_for_vitals(values, streak)
    # Exercise compliance offsets most modeled loss; poor sleep and elevated
    # radiation add small illustrative stress terms, with bounded daily changes.
    exercise_factor = min(values["exercise_mins"] / 120.0, 1.0)
    bone = day["space_metrics"]["bone_density_pct"]["value"]
    muscle = day["space_metrics"]["muscle_strength_pct"]["value"]
    bone = max(85.0, min(100.0, bone + (exercise_factor - 0.8) * 0.04 - (0.02 if values["sleep_hours"] < 5 else 0)))
    muscle = max(70.0, min(100.0, muscle + (exercise_factor - 0.8) * 0.18 - (0.08 if values["sleep_hours"] < 5 else 0)))
    old_v = day["vitals"]
    baselines = {"heart_rate_bpm": 72, "body_temp_c": 36.7, "blood_sugar_mgdl": 88, "spo2_pct": 98, "sleep_hours": 6.8, "mood_score": 8}
    day["inputs"] = {"exercise_mins": values["exercise_mins"], "sleep_hours": values["sleep_hours"], "mood_score": values["mood_score"]}
    day["vitals"] = {
        "heart_rate_bpm": {"value": values["heart_rate_bpm"], "unit": "bpm", "status": statuses["heart_rate_bpm"], "baseline": baselines["heart_rate_bpm"]},
        "blood_pressure": {"value": f"{values['bp_systolic']}/{values['bp_diastolic']}", "systolic": values["bp_systolic"], "diastolic": values["bp_diastolic"], "unit": "mmHg", "status": statuses["blood_pressure"]},
        "body_temp_c": {"value": values["body_temp_c"], "unit": "°C", "status": statuses["body_temp_c"], "baseline": baselines["body_temp_c"]},
        "blood_sugar_mgdl": {"value": values["blood_sugar_mgdl"], "unit": "mg/dL", "status": statuses["blood_sugar_mgdl"], "baseline": baselines["blood_sugar_mgdl"]},
        "spo2_pct": {"value": values["spo2_pct"], "unit": "%", "status": statuses["spo2_pct"], "baseline": baselines["spo2_pct"]},
        "sleep_hours": {"value": values["sleep_hours"], "unit": "hrs", "status": statuses["sleep_hours"], "baseline": baselines["sleep_hours"]},
        "mood_score": {"value": values["mood_score"], "unit": "/10", "status": statuses["mood_score"], "baseline": baselines["mood_score"]},
    }
    m = day["space_metrics"]
    m["bone_density_pct"].update(value=round(bone, 1), status=color(bone, 95, 91))
    m["muscle_strength_pct"].update(value=round(muscle, 1), status=color(muscle, 85, 80))
    m["mii_score"].update(value=round(0.6 * bone + 0.4 * muscle, 1), status=color(0.6 * bone + 0.4 * muscle, 95, 91))
    m["vo2_max"].update(value=round(max(25.0, min(45.0, m["vo2_max"]["value"] + (exercise_factor - 0.8) * 0.15)), 1))
    m["vo2_max"]["status"] = "green" if m["vo2_max"]["value"] >= 38 else "amber" if m["vo2_max"]["value"] >= 33 else "red"
    update_systems(day)
    day["alerts"] = make_alerts(day)
    day["action_cards"] = [alert["action"] for alert in day["alerts"]]
    record_action("Daily check-in recalculated")
    day["action_log"] = action_log[-10:]
    persist()
    return day


@app.get("/")
def index():
    return send_from_directory(ROOT, LIFELINE_PAGE, mimetype="text/html")


@app.get("/api/clinical/state")
def get_clinical_state():
    return jsonify(clinical_state)


@app.post("/api/clinical/evaluate")
def evaluate_clinical_input():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    try:
        values = validate_vitals(payload.get("vitals"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(evaluate_vitals(values))


@app.get("/api/clinical/catalog")
def get_clinical_catalog():
    with PROTOCOL_FILE.open(encoding="utf-8") as stream:
        catalog = json.load(stream)
    catalog["available_source_files"] = local_pdf_sources(ROOT)
    catalog["source_documents_available"] = bool(catalog.get("sources"))
    return jsonify(catalog)


def persist_clinical_state() -> None:
    CLINICAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CLINICAL_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(clinical_state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(CLINICAL_FILE)


def mission_start_date() -> str:
    return str(history[0]["date"])


@app.post("/api/clinical/vitals")
def update_clinical_vitals():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    with lock:
        try:
            values = validate_vitals(payload.get("vitals"))
            symptoms = validate_symptoms(payload.get("symptoms", clinical_state.get("symptoms", [])))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        clinical_state.update(vitals=values, symptoms=symptoms, scenario="manual_entry", triage=evaluate_vitals(values))
        add_audit_event(clinical_state, mission_start_date(), "vitals_logged", f"Manual readings recorded; triage={clinical_state['triage']['urgency']}")
        persist_clinical_state()
        return jsonify(clinical_state)


@app.post("/api/clinical/symptoms")
def update_clinical_symptoms():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    try:
        symptoms = validate_symptoms(payload.get("symptoms"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with lock:
        clinical_state["symptoms"] = symptoms
        add_audit_event(clinical_state, mission_start_date(), "symptoms_checked", f"Reported symptoms: {', '.join(symptoms) or 'none'}")
        persist_clinical_state()
        return jsonify(clinical_state)


@app.post("/api/clinical/scenario/<scenario_name>")
def clinical_scenario(scenario_name: str):
    if scenario_name not in SCENARIOS:
        return jsonify({"error": f"scenario_name must be one of: {', '.join(SCENARIOS)}"}), 404
    with lock:
        preset = SCENARIOS[scenario_name]
        values = validate_vitals(preset["vitals"])
        symptoms = validate_symptoms(preset["symptoms"])
        clinical_state.update(vitals=values, symptoms=symptoms, scenario=scenario_name, triage=evaluate_vitals(values))
        add_audit_event(clinical_state, mission_start_date(), "scenario_loaded", f"{preset['label']} (simulated pattern; not a diagnosis)")
        persist_clinical_state()
        return jsonify(clinical_state)


@app.post("/api/clinical/audit")
def add_clinical_audit_event():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    event_type, detail = payload.get("type"), payload.get("detail")
    allowed = {"symptoms_checked", "checklist_step", "medication_reported"}
    if event_type not in allowed or not isinstance(detail, str) or not detail.strip():
        return jsonify({"error": "Provide a supported event type and non-empty detail"}), 400
    if event_type == "checklist_step":
        with PROTOCOL_FILE.open(encoding="utf-8") as stream:
            catalog = json.load(stream)
        if not any(step.get("id") == payload.get("step_id") for protocol in catalog.get("protocols", []) for step in protocol.get("steps", [])):
            return jsonify({"error": "Checklist step is not present in the locally sourced protocol catalog"}), 400
    with lock:
        add_audit_event(clinical_state, mission_start_date(), event_type, detail.strip())
        persist_clinical_state()
        return jsonify(clinical_state)


@app.get("/api/history")
def get_history():
    return jsonify(history)


@app.get("/api/day/<int:day_num>")
def get_day(day_num: int):
    if day_num < 1 or day_num > len(history):
        return jsonify({"error": "day_num must be between 1 and 115"}), 404
    return jsonify(history[day_num - 1])


@app.post("/api/checkin")
def checkin():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400
    with lock:
        try:
            return jsonify(apply_inputs(payload))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@app.post("/api/scenario/<scenario_name>")
def scenario(scenario_name: str):
    presets = {"nominal", "solar_storm", "atrophy_risk"}
    if scenario_name not in presets:
        return jsonify({"error": "scenario_name must be nominal, solar_storm, or atrophy_risk"}), 404
    with lock:
        day = active_day()
        if scenario_name == "nominal":
            # Restore the generated nominal Day 115 snapshot and then reapply
            # the currently selected environment's rate and latency.
            day.clear()
            day.update(generate_history()[-1])
            day["environment"] = ENVIRONMENTS[active_environment]["label"]
            day.update(comms_fields(active_distance_km))
            record_action("Nominal scenario activated")
        elif scenario_name == "solar_storm":
            m = day["space_metrics"]
            m["radiation_msv"]["value"] = round(m["radiation_msv"]["value"] + 240, 1)
            m["radiation_msv"]["daily_rate"] = round(m["radiation_msv"]["daily_rate"] + 240, 1)
            m["radiation_msv"]["status"] = "amber" if m["radiation_msv"]["value"] < 500 else "red"
            v = day["vitals"]
            v["heart_rate_bpm"].update(value=94, status="amber")
            v["mood_score"].update(value=5, status="amber")
            day["inputs"].update(mood_score=5)
            day["alerts"] = [{"severity": "red" if m["radiation_msv"]["status"] == "red" else "amber", "domain": "Radiation", "title": "Solar Particle Event", "explanation": "Demo injects a +240 mSv SPE dose; current cumulative total is shown above.", "action": "Move immediately to the designated storm shelter; maximize water-equivalent shielding and follow flight surgeon guidance.", "nasa_ref": "NASA-STD-3001 [V1 4031]"}]
            record_action("Solar storm scenario: +240 mSv SPE, HR 94, mood 5")
        else:
            m = day["space_metrics"]
            m["bone_density_pct"].update(value=93.4, status="amber")
            m["muscle_strength_pct"].update(value=78.5, status="red")
            m["vo2_max"].update(value=32.1, status="red")
            m["mii_score"].update(value=round(0.6 * 93.4 + 0.4 * 78.5, 1), status="amber")
            day["inputs"].update(exercise_mins=0, sleep_hours=4.8)
            day["vitals"]["sleep_hours"].update(value=4.8, status="red")
            day["alerts"] = [{"severity": "red", "domain": "Musculoskeletal", "title": "Atrophy Risk", "explanation": "Demo preset represents 14 days without ARED exercise and restricted sleep.", "action": "Coordinate with the flight surgeon; resume progressive supervised ARED work and protect sleep opportunity.", "nasa_ref": "NASA-STD-3001 [V1 4024, V1 4027]"}]
            record_action("Atrophy-risk scenario activated: 14 missed workout days")
        update_systems(day)
        day["action_cards"] = [alert["action"] for alert in day["alerts"]]
        day["action_log"] = action_log[-10:]
        persist()
        return jsonify(day)


@app.post("/api/environment")
def set_environment():
    global active_environment, active_distance_km
    payload = request.get_json(silent=True)
    name = payload.get("environment") if isinstance(payload, dict) else None
    if name not in ENVIRONMENTS:
        return jsonify({"error": "environment must be ISS_LEO, MARS_TRANSIT, LUNAR_SURFACE, or MARS_HABITAT"}), 400
    # Optional override; otherwise use the environment's default distance.
    if "earth_distance_km" in payload:
        try:
            distance_km = finite_number(payload, "earth_distance_km", 0, MAX_DISTANCE_KM)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
    else:
        distance_km = DEFAULT_DISTANCE_KM[name]
    with lock:
        active_environment = name
        active_distance_km = distance_km
        env = ENVIRONMENTS[name]
        day = active_day()
        day["environment"] = env["label"]
        day.update(comms_fields(distance_km))
        day["space_metrics"]["radiation_msv"]["daily_rate"] = env["rate"]
        # Environment switch changes modeled daily rate; accumulated dose is
        # preserved. A solar-event increment already present remains included.
        day["space_metrics"]["radiation_msv"]["status"] = "red" if day["space_metrics"]["radiation_msv"]["value"] >= 500 else "amber" if day["space_metrics"]["radiation_msv"]["value"] >= 250 else "green"
        record_action(f"Environment switched to {name}")
        day["action_log"] = action_log[-10:]
        update_systems(day)
        day["alerts"] = make_alerts(day)
        persist()
        return jsonify({"environment": name, "label": env["label"], "daily_radiation_rate_msv": env["rate"], **comms_fields(distance_km), "day": day})


@app.get("/api/flight-surgeon-packet")
def flight_surgeon_packet():
    day = active_day()
    m, v = day["space_metrics"], day["vitals"]
    anomalies = [f"{key}={status}" for key, status in day["body_systems"].items() if status != "green"]
    text = "\n".join([
        "CREW HEALTH BURST | DEMO",
        f"DAY={day['day']} DATE={day['date']} ENV={active_environment}",
        f"RAD={m['radiation_msv']['value']:.1f}mSv RATE={m['radiation_msv']['daily_rate']:.1f}mSv/d",
        f"BONE={m['bone_density_pct']['value']:.1f}% MUSCLE={m['muscle_strength_pct']['value']:.1f}% MII={m['mii_score']['value']:.1f}% VO2={m['vo2_max']['value']:.1f}",
        f"HR={v['heart_rate_bpm']['value']} BP={v['blood_pressure']['value']} TEMP={v['body_temp_c']['value']} SPO2={v['spo2_pct']['value']} SLEEP={v['sleep_hours']['value']} MOOD={v['mood_score']['value']}",
        f"CLINICAL_TRIAGE={clinical_state['triage']['urgency'].upper()} FLAGS=" + (",".join(flag["id"] for flag in clinical_state["triage"]["flags"]) or "NONE"),
        "RECENT_MET=" + ";".join(f"{event['met']}:{event['type']}" for event in clinical_state.get("audit", [])[-3:]),
        "ANOMALY=" + (",".join(anomalies) if anomalies else "NONE"),
        "ACTION=" + (day.get("action_cards", [day["alerts"][0]["action"]])[0]),
        "REF=PROJECT DEMO THRESHOLDS | SIMULATION ONLY",
    ])
    response = make_response(text, 200)
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["X-Packet-Bytes"] = str(len(text.encode("utf-8")))
    return response


def on_telemetry_urgency_change(old: str | None, new: str, detail: str) -> None:
    """Record wearable triage CHANGES in the MET audit log. Individual readings
    are not logged - at ~1 per second they would push everything else out of
    the 100-entry log within two minutes."""
    with lock:
        add_audit_event(clinical_state, mission_start_date(), "telemetry_triage",
                        f"Wearable triage {old or 'start'} -> {new}: {detail}")
        persist_clinical_state()


# Simulated wearable telemetry (see telemetry.py and simulator.py).
telemetry_bp = create_telemetry_blueprint(on_telemetry_urgency_change)
app.register_blueprint(telemetry_bp)

# Emergency checklist lookup (see checklist.py): crew taps what they see, the
# live wearable vitals fill in the rest, matching procedure PDFs come up.
app.register_blueprint(create_checklist_blueprint(
    live_summary=telemetry_bp.crew.summary,
    comms_delay=lambda: active_day().get("comms_delay_seconds", 0),
))


# Lifeline emergency page (Lifeline_Emergency.html, a teammate's design).
# The page polls /api/lifeline/live for the wearable feed and comms state,
# and posts crew inputs to /api/checklist/match.
LIFELINE_PAGE = "Lifeline_Emergency.html"


@app.get("/lifeline")
def lifeline_page():
    return send_from_directory(ROOT, LIFELINE_PAGE, mimetype="text/html")


@app.get("/api/lifeline/live")
def lifeline_live():
    """One poll for everything live the page shows: every crewmember's
    telemetry summary (medians, sources, triage flags) plus comms. The page
    never judges readings itself."""
    with lock:
        day = active_day()
        comms = {
            "environment": active_environment,
            "label": ENVIRONMENTS[active_environment]["label"],
            **comms_fields(active_distance_km),
        }
        mission = {"day": day.get("day"), "date": day.get("date")}
    return jsonify({"crew": telemetry_bp.crew.all(), "default_crew": telemetry_bp.crew.default,
                    "comms": comms, "mission": mission})


with lock:
    history = load_history()
    # Recompute the delay from distance, so older saved data with a fixed
    # comms_delay_seconds picks up the speed-of-light value.
    for record in history:
        record.update(comms_fields(active_distance_km))
    if CLINICAL_FILE.is_file():
        with CLINICAL_FILE.open(encoding="utf-8") as stream:
            clinical_state = json.load(stream)
        clinical_state["vitals"] = validate_vitals(clinical_state.get("vitals"))
        clinical_state["symptoms"] = validate_symptoms(clinical_state.get("symptoms", []))
        clinical_state["triage"] = evaluate_vitals(clinical_state["vitals"])
        clinical_state.setdefault("audit", [])
    else:
        clinical_state = default_clinical_state(history[-1])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
