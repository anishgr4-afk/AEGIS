import json

import pytest

from conftest import PASSWORD, failure, success


@pytest.mark.parametrize("username,expected_role", [
    ("commander", "Commander"), ("medical", "Medical Officer"),
    ("operator", "Mission Operator"), ("admin", "Administrator"),
])
def test_demo_login_session_and_no_secrets(client, username, expected_role):
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    data = success(response, (200,))
    assert data["user"]["username"] == username
    assert data["user"]["role"] == expected_role
    assert data["token"]
    lowered = json.dumps(data["user"]).lower()
    assert "password" not in lowered and "hash" not in lowered and PASSWORD not in lowered
    session = client.get("/api/auth/session", headers={"Authorization": f"Bearer {data['token']}"})
    user = success(session, (200,))
    assert user.get("username") == username
    assert PASSWORD not in json.dumps(user)


@pytest.mark.parametrize("payload", [
    {"username": "admin", "password": "wrong"},
    {"username": "not-a-user", "password": PASSWORD},
    {"username": "admin"}, {},
])
def test_invalid_login_is_rejected(client, payload):
    failure(client.post("/api/auth/login", json=payload), (400, 401, 422))


def test_health_public_and_protected_routes_need_authentication(api):
    success(api.get("/api/health", role=None), (200,))
    for route in ("/api/auth/session", "/api/snapshot", "/api/crew", "/api/telemetry", "/api/medical-records", "/api/audit", "/api/users"):
        failure(api.get(route, role=None), (401,))
    failure(api.get("/api/snapshot", token="definitely-not-a-valid-session"), (401,))


def test_logout_invalidates_token(api):
    token = api.login("operator")
    success(api.post("/api/auth/logout", token=token), (200,))
    failure(api.get("/api/auth/session", token=token), (401,))


@pytest.mark.parametrize("role", ["commander", "operator"])
@pytest.mark.parametrize("path", ["/api/medical-records", "/api/medications", "/api/administrations"])
def test_sensitive_collections_forbidden_to_non_medical_roles(api, role, path):
    failure(api.get(path, role=role), (403,))
    failure(api.post(path, role=role, json={}), (403,)) if path != "/api/administrations" else None


@pytest.mark.parametrize("role", ["commander", "medical", "operator"])
@pytest.mark.parametrize("path", ["/api/audit", "/api/users"])
def test_audit_and_user_administration_are_admin_only(api, role, path):
    failure(api.get(path, role=role), (403,))


def test_operator_cannot_escalate_role_or_change_settings(api):
    users = success(api.get("/api/users"))
    operator = next(row for row in users if row["username"] == "operator")
    failure(api.patch(f"/api/users/{operator['id']}", role="operator", json={"role": "Administrator"}), (403,))
    failure(api.patch("/api/settings", role="operator", json={"mission_day": 999}), (403,))
    user = success(api.get("/api/auth/session", role="operator"))
    assert user["role"] == "Mission Operator"


def test_medical_notes_are_filtered_from_snapshot_crew_and_events(api, crew_id):
    marker = "sensitive-note-DO-NOT-LEAK-78112"
    success(api.post("/api/medical-records", role="medical", json={"crew_id": crew_id, "category": "observation", "note": marker, "follow_up": "review"}))
    for role in ("operator", "commander"):
        for route in ("/api/snapshot", "/api/crew", "/api/events"):
            data = success(api.get(route, role=role))
            assert marker not in json.dumps(data), f"sensitive medical note leaked in {route} to {role}"


def test_sensitive_crew_profile_fields_are_filtered_for_nonmedical_users(api, crew_id):
    marker = "pytest-private-crew-history-183694"
    success(api.patch(f"/api/crew/{crew_id}", role="medical", json={"baseline": marker, "medical_history": marker, "current_health": marker}))
    medical_crew = success(api.get("/api/crew", role="medical"))
    assert marker in json.dumps(medical_crew)
    for role in ("commander", "operator"):
        for route in ("/api/crew", "/api/snapshot", "/api/events"):
            assert marker not in json.dumps(success(api.get(route, role=role)))


@pytest.mark.parametrize("role", ["commander", "operator"])
def test_nonmedical_roles_cannot_mutate_medical_records_or_administer_medication(api, crew_id, role):
    record = success(api.post("/api/medical-records", role="medical", json={"crew_id": crew_id, "category": "observation", "note": "original", "follow_up": ""}))
    medication = success(api.post("/api/medications", role="medical", json={"name": "test item", "quantity": 3, "unit": "vial", "expiry_date": "2030-01-01", "storage": "cabinet", "low_stock_threshold": 0}))
    failure(api.patch(f"/api/medical-records/{record['id']}", role=role, json={"note": "tampered"}), (403,))
    failure(api.post(f"/api/medications/{medication['id']}/administer", role=role, json={"crew_id": crew_id, "quantity": 1, "note": "unauthorized"}), (403,))
    stored_record = next(r for r in success(api.get("/api/medical-records", role="medical")) if r["id"] == record["id"])
    stored_medication = next(r for r in success(api.get("/api/medications", role="medical")) if r["id"] == medication["id"])
    assert stored_record["note"] == "original" and stored_medication["quantity"] == 3
