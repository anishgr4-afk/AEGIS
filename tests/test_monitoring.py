import pytest

from conftest import failure, parse_utc, success, utc_now


def configure(api, metric, limits):
    settings = success(api.get("/api/settings"))
    thresholds = dict(settings["thresholds"])
    thresholds[metric] = limits
    changed = success(api.patch("/api/settings", json={"thresholds": thresholds}))
    assert changed["thresholds"][metric] == limits


def matching_alerts(api, entity_type, metric, value):
    alerts = success(api.get("/api/alerts"))
    assert isinstance(alerts, list)
    return [a for a in alerts if a.get("entity_type") == entity_type and a.get("metric") == metric and a.get("value") == value]


def test_configured_telemetry_alert_severity_acknowledge_and_resolve(api, telemetry):
    configure(api, "heart_rate", {"min": 40, "max": 100, "critical_min": 30, "critical_max": 120, "unit": "bpm"})
    success(api.post("/api/telemetry", json={**telemetry, "value": 110}))
    warning = matching_alerts(api, "crew", "heart_rate", 110)
    assert warning and warning[-1]["severity"] == "WARNING"
    success(api.post("/api/telemetry", json={**telemetry, "value": 130}))
    critical = matching_alerts(api, "crew", "heart_rate", 130)
    assert critical and critical[-1]["severity"] == "CRITICAL"
    alert = critical[-1]
    assert alert["entity_id"] == telemetry["crew_id"]
    for field in ("id", "unit", "rule", "guidance"):
        assert alert.get(field), f"missing alert field {field}"
    parse_utc(alert["detected_at"])
    assert not alert["acknowledged"] and not alert["resolved"]
    acknowledged = success(api.post(f"/api/alerts/{alert['id']}/acknowledge", role="medical"))
    assert acknowledged["acknowledged"] is True
    resolved = success(api.post(f"/api/alerts/{alert['id']}/resolve", role="medical"))
    assert resolved["resolved"] is True
    historical = matching_alerts(api, "crew", "heart_rate", 130)
    assert any(a["id"] == alert["id"] and a["acknowledged"] and a["resolved"] for a in historical)


def test_normal_measurement_does_not_generate_out_of_range_alert(api, telemetry):
    configure(api, "heart_rate", {"min": 40, "max": 100, "critical_min": 30, "critical_max": 120, "unit": "bpm"})
    before = len(matching_alerts(api, "crew", "heart_rate", 75))
    success(api.post("/api/telemetry", json={**telemetry, "value": 75}))
    assert len(matching_alerts(api, "crew", "heart_rate", 75)) == before


def test_environment_threshold_creates_distinct_alert(api):
    configure(api, "oxygen", {"min": 19, "max": 23, "critical_min": 17, "critical_max": 25, "unit": "%"})
    observation = {"metric": "oxygen", "value": 26, "unit": "%", "source": "pytest-cabin", "timestamp": utc_now()}
    created = success(api.post("/api/environment", json=observation))
    assert created["metric"] == "oxygen" and created["source"] == observation["source"]
    assert parse_utc(created["timestamp"]) == parse_utc(observation["timestamp"])
    alerts = matching_alerts(api, "environment", "oxygen", 26)
    assert alerts and alerts[-1]["severity"] == "CRITICAL"
    assert alerts[-1]["unit"] == "%"


@pytest.mark.parametrize("changes", [
    {"metric": "unsupported"}, {"value": "bad"}, {"value": None},
    {"timestamp": "yesterday-ish"}, {"source": ""}, {"unit": ""},
])
def test_invalid_environment_observations_are_rejected(api, changes):
    valid = {"metric": "oxygen", "value": 20, "unit": "%", "source": "cabin sensor", "timestamp": utc_now()}
    before = success(api.get("/api/environment"))
    failure(api.post("/api/environment", json={**valid, **changes}))
    assert len(success(api.get("/api/environment"))) == len(before)


def test_radiation_nonnegative_increments_accumulate_in_snapshot(api, crew_id):
    before = success(api.get("/api/snapshot"))["mission"]["radiation_total"]
    ids = []
    for dose in (1.25, 2.75):
        payload = {"crew_id": crew_id, "value": dose, "unit": "mSv", "source": "pytest-dosimeter", "timestamp": utc_now(), "event": "test exposure"}
        row = success(api.post("/api/radiation", json=payload))
        ids.append(row["id"])
        assert row["value"] == dose and row["unit"] == "mSv" and row["source"] == payload["source"]
        assert parse_utc(row["timestamp"]) == parse_utc(payload["timestamp"])
    assert len(set(ids)) == 2
    after = success(api.get("/api/snapshot"))["mission"]["radiation_total"]
    assert float(after) == pytest.approx(float(before) + 4.0)


@pytest.mark.parametrize("changes", [{"value": -1}, {"value": "NaN"}, {"unit": "Sv"}, {"source": ""}, {"timestamp": "invalid"}])
def test_invalid_radiation_is_rejected(api, crew_id, changes):
    payload = {"crew_id": crew_id, "value": 1, "unit": "mSv", "source": "dosimeter", "timestamp": utc_now()}
    before = success(api.get("/api/radiation"))
    failure(api.post("/api/radiation", json={**payload, **changes}))
    assert len(success(api.get("/api/radiation"))) == len(before)


def test_cumulative_radiation_rule_and_per_crew_total(api, crew_id):
    configure(api, "radiation_cumulative", {"min": 0, "max": 1, "critical_min": 0, "critical_max": 2, "unit": "mSv"})
    before = success(api.get("/api/snapshot"))["mission"]
    dose = 2.5
    success(api.post("/api/radiation", json={"crew_id": crew_id, "value": dose, "unit": "mSv", "source": "pytest-dose", "timestamp": utc_now()}))
    after = success(api.get("/api/snapshot"))["mission"]
    assert after["radiation_by_crew"][crew_id] == pytest.approx(before["radiation_by_crew"].get(crew_id, 0) + dose)
    assert after["radiation_daily"][crew_id] == pytest.approx(before["radiation_daily"].get(crew_id, 0) + dose)
    alerts = success(api.get("/api/alerts"))
    assert any(a.get("entity_type") == "radiation" and a.get("entity_id") == crew_id and a.get("metric") == "radiation_cumulative" and a.get("severity") == "CRITICAL" for a in alerts)
