import hashlib
import json
import sqlite3

import pytest

from conftest import PASSWORD, failure, success


def test_expired_session_is_rejected_by_server(api, app_factory):
    token = api.login("operator")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    # Force expiry in the isolated test database; no real waiting is needed.
    with sqlite3.connect(app_factory.db_path) as db:
        changed = db.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?", ("2000-01-01T00:00:00Z", digest)).rowcount
        db.commit()
    assert changed == 1
    failure(api.get("/api/snapshot", token=token), (401,))


def test_denied_access_is_audited_without_credentials_or_token(api):
    token = api.login("operator")
    before = success(api.get("/api/audit"))
    failure(api.get("/api/audit", token=token), (403,))
    after = success(api.get("/api/audit"))
    assert len(after) > len(before), "Forbidden access should create a security audit entry"
    new_entries = after[len(before):]
    assert any("denied" in str(entry.get("action", "")).lower() or "forbidden" in str(entry.get("outcome", "")).lower() for entry in new_entries)
    assert token not in json.dumps(after) and PASSWORD not in json.dumps(after)


def test_admin_user_creation_hashes_password_and_validates_role(api, app_factory):
    password = "pytest-not-a-real-secret-123"
    created = success(api.post("/api/users", json={"username": "pytest_user", "display_name": "Test User", "role": "Mission Operator", "password": password}))
    assert created["username"] == "pytest_user" and created["role"] == "Mission Operator"
    assert password not in json.dumps(created) and "password" not in json.dumps(created).lower()
    with sqlite3.connect(app_factory.db_path) as db:
        stored = db.execute("SELECT password_hash FROM users WHERE username=?", ("pytest_user",)).fetchone()
    assert stored and stored[0] != password and password not in stored[0]
    logged_in = success(api.client.post("/api/auth/login", json={"username": "pytest_user", "password": password}))
    assert logged_in["user"]["role"] == "Mission Operator"
    failure(api.post("/api/users", json={"username": "pytest_other", "role": "Root", "password": password}))
    failure(api.post("/api/users", json={"username": "pytest_user", "role": "Commander", "password": password}), (400, 409, 422))


def test_offline_measurement_still_generates_local_critical_alert(api, telemetry):
    success(api.patch("/api/settings", json={"earth_connected": False}))
    settings = success(api.get("/api/settings"))
    limits = dict(settings["thresholds"])
    limits["heart_rate"] = {"min": 40, "max": 100, "critical_min": 30, "critical_max": 120, "unit": "bpm"}
    success(api.patch("/api/settings", json={"thresholds": limits}))
    reading = {**telemetry, "value": 150, "source": "offline-test-sensor"}
    success(api.post("/api/telemetry", json=reading))
    alerts = success(api.get("/api/alerts"))
    assert any(a.get("entity_type") == "crew" and a.get("entity_id") == telemetry["crew_id"] and a.get("metric") == "heart_rate" and a.get("value") == 150 and a.get("severity") == "CRITICAL" for a in alerts)


@pytest.mark.parametrize("bad_rule", [
    {"min": 120, "max": 100, "critical_min": 30, "critical_max": 150, "unit": "bpm"},
    {"min": "invalid", "max": 100, "critical_min": 30, "critical_max": 150, "unit": "bpm"},
])
def test_invalid_threshold_configuration_is_rejected_without_change(api, bad_rule):
    before = success(api.get("/api/settings"))
    thresholds = dict(before["thresholds"])
    thresholds["heart_rate"] = bad_rule
    failure(api.patch("/api/settings", json={"thresholds": thresholds}))
    after = success(api.get("/api/settings"))
    assert after["thresholds"] == before["thresholds"]
