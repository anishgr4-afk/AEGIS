"""Generate deterministic, offline mission telemetry for the health console.

The values are a demo simulation, not clinical measurements or operational
flight rules. The historical radiation curve is calibrated to the requested
~155 mSv Day 115 story arc; Day 78 includes a +35 mSv event.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from comms import DEFAULT_DISTANCE_KM, comms_fields

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "mission_data.json"
START_DATE = date(2026, 7, 23)  # Day 115 is 2026-11-14.


def band(value: float, green: float, amber: float) -> str:
    return "green" if value >= green else "amber" if value >= amber else "red"


def vital_statuses(hr: int, systolic: int, diastolic: int, temp: float,
                   glucose: int, spo2: int, sleep: float, mood: int,
                   short_sleep_streak: int) -> dict[str, str]:
    return {
        "heart_rate_bpm": "red" if hr > 100 or hr < 48 else "amber" if hr > 88 or hr < 55 else "green",
        "blood_pressure": "red" if systolic > 145 or diastolic > 92 or systolic < 90 or diastolic < 55 else "amber" if systolic > 130 or diastolic > 84 or systolic < 100 or diastolic < 65 else "green",
        "body_temp_c": "red" if temp > 38 or temp < 35.5 else "amber" if temp >= 37.5 or temp < 36.1 else "green",
        "blood_sugar_mgdl": "red" if glucose > 160 or glucose < 54 else "amber" if glucose > 125 or glucose < 65 else "green",
        "spo2_pct": "red" if spo2 < 92 else "amber" if spo2 < 95 else "green",
        "sleep_hours": "red" if sleep < 5 or short_sleep_streak >= 3 else "amber" if sleep < 6.5 else "green",
        "mood_score": "red" if mood <= 4 else "amber" if mood <= 6 else "green",
    }


def generate_history() -> list[dict]:
    history: list[dict] = []
    radiation = 0.0
    short_sleep_streak = 0
    for day in range(1, 116):
        if 41 <= day <= 55:
            exercise = [72, 0, 65, 0, 80, 0, 70, 60, 0, 85, 0, 75, 90, 0, 95][day - 41]
            sleep = round(5.5 + ((day * 7) % 8) / 10, 1)
        else:
            exercise = 115 + (day * 11 % 16) - 8
            sleep = round(6.6 + ((day * 3) % 7) / 10, 1)
        short_sleep_streak = short_sleep_streak + 1 if sleep < 6 else 0
        # 1.05 mSv/day gives a 120.75 mSv background across 115 days;
        # with the +35 mSv event the final history is ~155.8 mSv.
        radiation += 1.05
        daily_rate = 1.05
        event = day == 78
        if event:
            radiation += 35.0
            daily_rate += 35.0

        # Smooth loss with a temporary exercise-related dip and recovery.
        if day <= 40:
            bone = 100 - day * 0.021
            muscle = 100 - day * 0.055
        elif day <= 55:
            bone = 99.16 - (day - 40) * 0.12
            muscle = 97.8 - (day - 40) * 0.34
        elif day <= 78:
            bone = 97.36 + (day - 55) * (0.35 / 23)
            muscle = 92.7 + (day - 55) * (1.1 / 23)
        else:
            bone = 97.71 - (day - 78) * (1.51 / 37)
            muscle = 93.8 - (day - 78) * (4.3 / 37)
        bone = round(bone, 1)
        muscle = round(muscle, 1)
        vo2 = round(45 - day * 0.033, 1)
        hr = 72 + (day % 7 - 3)
        systolic, diastolic = 118 + day % 5, 76 + day % 4
        temp, glucose, spo2, mood = round(36.7 + ((day % 5) - 2) * 0.1, 1), 88 + day % 9, 97 + day % 3, 8 if day % 11 else 7
        statuses = vital_statuses(hr, systolic, diastolic, temp, glucose, spo2, sleep, mood, short_sleep_streak)
        mii = round(0.6 * bone + 0.4 * muscle, 1)
        rad_status = "red" if radiation >= 500 or (event and 35 >= 250) else "amber" if radiation >= 250 or event else "green"
        b_status, m_status = band(bone, 95, 91), band(muscle, 85, 80)
        v_status = "green" if vo2 >= 38 else "amber" if vo2 >= 33 else "red"
        systems = {
            "cardiovascular": statuses["heart_rate_bpm"] if statuses["heart_rate_bpm"] != "green" else statuses["blood_pressure"],
            "musculoskeletal": "red" if "red" in (b_status, m_status, v_status) else "amber" if "amber" in (b_status, m_status, v_status) else "green",
            "neuro_circadian": "red" if statuses["sleep_hours"] == "red" or statuses["mood_score"] == "red" else "amber" if "amber" in (statuses["sleep_hours"], statuses["mood_score"]) else "green",
            "metabolic_immune": "red" if "red" in (statuses["body_temp_c"], statuses["blood_sugar_mgdl"], statuses["spo2_pct"]) else "amber" if "amber" in (statuses["body_temp_c"], statuses["blood_sugar_mgdl"], statuses["spo2_pct"]) else "green",
            "radiation_cellular": rad_status,
        }
        vital_values = {
            "heart_rate_bpm": {"value": hr, "unit": "bpm", "status": statuses["heart_rate_bpm"], "baseline": 72},
            "blood_pressure": {"value": f"{systolic}/{diastolic}", "systolic": systolic, "diastolic": diastolic, "unit": "mmHg", "status": statuses["blood_pressure"]},
            "body_temp_c": {"value": temp, "unit": "°C", "status": statuses["body_temp_c"], "baseline": 36.7},
            "blood_sugar_mgdl": {"value": glucose, "unit": "mg/dL", "status": statuses["blood_sugar_mgdl"], "baseline": 88},
            "spo2_pct": {"value": spo2, "unit": "%", "status": statuses["spo2_pct"], "baseline": 98},
            "sleep_hours": {"value": sleep, "unit": "hrs", "status": statuses["sleep_hours"], "baseline": 6.8},
            "mood_score": {"value": mood, "unit": "/10", "status": statuses["mood_score"], "baseline": 8},
        }
        alerts = []
        if event:
            alerts.append({"severity": "amber", "domain": "Radiation", "title": "Minor Solar Particle Event", "explanation": "A simulated +35 mSv event is included in this day's cumulative dose.", "action": "Review exposure and shielding allocation; continue monitoring.", "nasa_ref": "NASA-STD-3001 [V1 4031]"})
        if day == 115:
            alerts.append({"severity": "green", "domain": "All Systems", "title": "Nominal Deep-Space Transit", "explanation": "Simulated vitals and countermeasures are within configured console thresholds.", "action": "Continue standard daily ARED/CEVIS protocol.", "nasa_ref": "NASA-STD-3001 [V1 4024, V1 4027]"})
        history.append({
            "day": day, "date": (START_DATE + timedelta(days=day - 1)).isoformat(),
            "environment": "Mars Transit (Deep Space)", **comms_fields(DEFAULT_DISTANCE_KM["MARS_TRANSIT"]),
            "inputs": {"exercise_mins": exercise, "sleep_hours": sleep, "mood_score": mood},
            "vitals": vital_values,
            "space_metrics": {
                "radiation_msv": {"value": round(radiation, 1), "daily_rate": round(daily_rate, 1), "limit": 600, "unit": "mSv", "status": rad_status},
                "bone_density_pct": {"value": bone, "limit": 95.0, "unit": "%", "status": b_status},
                "muscle_strength_pct": {"value": muscle, "limit": 80.0, "unit": "%", "status": m_status},
                "vo2_max": {"value": vo2, "limit": 32.9, "unit": "ml/min/kg", "status": v_status},
                "mii_score": {"value": mii, "unit": "%", "status": band(mii, 95, 91)},
            },
            "body_systems": systems, "alerts": alerts,
            "action_log": [],
        })
    return history


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(generate_history(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT} ({115} days)")


if __name__ == "__main__":
    main()
