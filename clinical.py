"""Deterministic demo triage rules and local clinical-state helpers.

These rules implement thresholds supplied in the project brief. They are not
NASA protocols, diagnoses, or treatment recommendations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SYMPTOMS = [
    {"id": "fever_chills", "label": "Fever / chills", "region": "systemic"},
    {"id": "weakness_fatigue", "label": "Weakness / fatigue", "region": "systemic"},
    {"id": "shortness_breath", "label": "Shortness of breath / wheezing", "region": "cardiopulmonary"},
    {"id": "chest_pain", "label": "Chest pain", "region": "cardiopulmonary"},
    {"id": "abdominal_pain", "label": "Abdominal pain", "region": "abdomen_gi"},
    {"id": "nausea_vomiting", "label": "Nausea / vomiting", "region": "abdomen_gi"},
    {"id": "headache_dizziness", "label": "Headache / dizziness", "region": "head_eent"},
    {"id": "joint_pain", "label": "Joint pain", "region": "musculoskeletal_skin"},
    {"id": "tingling", "label": "Tingling / numbness", "region": "musculoskeletal_skin"},
    {"id": "ear_sinus_pain", "label": "Ear / sinus pain", "region": "head_eent"},
    {"id": "skin_rash_burn", "label": "Skin rash / burn", "region": "musculoskeletal_skin"},
    {"id": "cough", "label": "Cough", "region": "cardiopulmonary"},
]
SYMPTOM_IDS = {item["id"] for item in SYMPTOMS}

VITAL_RANGES = {
    "body_temp_c": (30, 43),
    "spo2_pct": (50, 100),
    "heart_rate_bpm": (25, 220),
    "bp_systolic": (50, 250),
    "bp_diastolic": (25, 150),
    "respiratory_rate_bpm": (1, 80),
}

SCENARIOS: dict[str, dict[str, Any]] = {
    "nominal_transit": {
        "label": "Nominal transit",
        "vitals": {"body_temp_c": 36.5, "spo2_pct": 97, "heart_rate_bpm": 73, "bp_systolic": 118, "bp_diastolic": 76, "respiratory_rate_bpm": 16},
        "symptoms": [],
    },
    "acute_febrile": {
        "label": "Acute febrile pattern · simulation",
        "vitals": {"body_temp_c": 38.4, "spo2_pct": 94, "heart_rate_bpm": 104, "bp_systolic": 118, "bp_diastolic": 74, "respiratory_rate_bpm": 22},
        "symptoms": ["fever_chills", "weakness_fatigue", "cough"],
    },
    "post_eva_dcs": {
        "label": "Post-EVA symptoms · simulation",
        "vitals": {"body_temp_c": 36.8, "spo2_pct": 97, "heart_rate_bpm": 98, "bp_systolic": 122, "bp_diastolic": 78, "respiratory_rate_bpm": 22},
        "symptoms": ["joint_pain", "tingling", "headache_dizziness"],
    },
    "hypovolemic_shock": {
        "label": "Low blood pressure pattern · simulation",
        "vitals": {"body_temp_c": 36.4, "spo2_pct": 91, "heart_rate_bpm": 125, "bp_systolic": 84, "bp_diastolic": 52, "respiratory_rate_bpm": 26},
        "symptoms": ["weakness_fatigue", "headache_dizziness"],
    },
}


def validate_vitals(raw: Any) -> dict[str, float | int]:
    if not isinstance(raw, dict):
        raise ValueError("vitals must be a JSON object")
    values: dict[str, float | int] = {}
    for key, (minimum, maximum) in VITAL_RANGES.items():
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        values[key] = int(value) if key != "body_temp_c" else round(float(value), 1)
    return values


def validate_symptoms(raw: Any) -> list[str]:
    if not isinstance(raw, list) or any(not isinstance(item, str) or item not in SYMPTOM_IDS for item in raw):
        raise ValueError("symptoms must be a list of supported symptom ids")
    return list(dict.fromkeys(raw))


def evaluate_vitals(v: dict[str, float | int]) -> dict[str, Any]:
    flags: list[dict[str, str]] = []

    def add(key: str, label: str, value: Any, unit: str, severity: str, threshold: str) -> None:
        flags.append({"id": key, "label": label, "value": str(value), "unit": unit, "severity": severity, "threshold": threshold})

    temp = float(v["body_temp_c"])
    if temp > 38.0:
        add("body_temp_c", "Body temperature", temp, "°C", "act", "> 38.0 °C · supplied demo threshold")
    elif temp >= 37.5:
        add("body_temp_c", "Body temperature", temp, "°C", "watch", "37.5–38.0 °C · supplied demo threshold")

    hr = int(v["heart_rate_bpm"])
    if hr > 100:
        add("heart_rate_bpm", "Heart rate", hr, "bpm", "act", "> 100 bpm · supplied demo threshold")
    elif hr > 88:
        add("heart_rate_bpm", "Heart rate", hr, "bpm", "watch", "89–100 bpm · supplied demo threshold")
    elif hr < 48:
        add("heart_rate_bpm", "Heart rate", hr, "bpm", "act", "< 48 bpm · supplied demo threshold")
    elif hr < 60:
        add("heart_rate_bpm", "Heart rate", hr, "bpm", "watch", "< 60 bpm · supplied brief trigger")

    sys, dia = int(v["bp_systolic"]), int(v["bp_diastolic"])
    if sys > 145 or dia > 92:
        add("blood_pressure", "Blood pressure", f"{sys}/{dia}", "mmHg", "act", "> 145 systolic or > 92 diastolic")
    elif sys < 90 or dia < 55:
        add("blood_pressure", "Blood pressure", f"{sys}/{dia}", "mmHg", "act", "< 90 systolic or < 55 diastolic")
    elif sys > 130 or dia > 84 or sys < 100 or dia < 65:
        add("blood_pressure", "Blood pressure", f"{sys}/{dia}", "mmHg", "watch", "Outside the supplied nominal range")

    spo2 = int(v["spo2_pct"])
    if spo2 < 92:
        add("spo2_pct", "SpO₂", spo2, "%", "act", "< 92% · supplied demo threshold")
    elif spo2 < 95:
        add("spo2_pct", "SpO₂", spo2, "%", "watch", "92–94% · supplied demo threshold")

    rr = int(v["respiratory_rate_bpm"])
    if rr < 12 or rr > 20:
        add("respiratory_rate_bpm", "Respiratory rate", rr, "breaths/min", "watch", "Outside 12–20 breaths/min · supplied nominal range")

    urgency = "act" if any(f["severity"] == "act" for f in flags) else "watch" if flags else "routine"
    return {"urgency": urgency, "flags": flags}


def default_clinical_state(day: dict[str, Any]) -> dict[str, Any]:
    vitals = day.get("vitals", {})
    bp = vitals.get("blood_pressure", {})
    values = validate_vitals({
        "body_temp_c": vitals.get("body_temp_c", {}).get("value", 36.5),
        "spo2_pct": vitals.get("spo2_pct", {}).get("value", 97),
        "heart_rate_bpm": vitals.get("heart_rate_bpm", {}).get("value", 73),
        "bp_systolic": bp.get("systolic", 118),
        "bp_diastolic": bp.get("diastolic", 76),
        "respiratory_rate_bpm": 16,
    })
    return {"vitals": values, "symptoms": [], "scenario": "nominal_transit", "triage": evaluate_vitals(values), "audit": []}


def met_timestamp(mission_start: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    start = datetime.fromisoformat(mission_start).replace(tzinfo=timezone.utc)
    elapsed = max(0, int((now - start).total_seconds()))
    days, remainder = divmod(elapsed, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days:03d}:{hours:02d}:{minutes:02d}:{seconds:02d}"


def add_audit_event(state: dict[str, Any], mission_start: str, event_type: str, detail: str) -> dict[str, str]:
    event = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "met": met_timestamp(mission_start),
        "type": event_type,
        "detail": detail[:240],
    }
    state.setdefault("audit", []).append(event)
    state["audit"] = state["audit"][-100:]
    return event


def local_pdf_sources(root: Path) -> list[dict[str, str]]:
    source_root = root / "medical_sources"
    paths = list(source_root.rglob("*.pdf")) if source_root.exists() else []
    # During local development the two supplied documents may sit beside the
    # project directory. Report only these expected filenames, never a broad
    # parent-directory scan.
    for filename in ("Medical Checklist Rev H.pdf", "nasa-std-3001-vol-1-rev-c-with-signature.pdf"):
        sibling = root.parent / filename
        if sibling.is_file():
            paths.append(sibling)
    output = []
    for path in sorted(set(paths)):
        try:
            display_path = path.relative_to(root).as_posix()
        except ValueError:
            display_path = f"../{path.name}"
        output.append({"path": display_path, "status": "source_text_indexed_requires_clinical_review"})
    return output
