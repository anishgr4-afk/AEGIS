import json

from conftest import failure, success


def test_medical_record_revision_history_and_audit_are_retained(api, crew_id):
    original_note = "pytest-original-observation-47281"
    updated_note = "pytest-revised-observation-47281"
    payload = {"crew_id": crew_id, "category": "observation", "note": original_note, "follow_up": "review"}
    created = success(api.post("/api/medical-records", role="medical", json=payload))
    record_id = created["id"]
    initial_audit = success(api.get("/api/audit"))
    updated = success(api.patch(f"/api/medical-records/{record_id}", role="medical", json={"note": updated_note}))
    assert updated["note"] == updated_note
    records = success(api.get("/api/medical-records", role="medical"))
    record = next(item for item in records if item["id"] == record_id)
    assert record["note"] == updated_note
    assert original_note in json.dumps(record), "Authorized medical record must preserve its earlier revision"
    audit = success(api.get("/api/audit"))
    assert len(audit) > len(initial_audit)
    assert any(str(record_id) in json.dumps(entry) for entry in audit), "Medical change must be auditable"
    # New audit events cannot mutate or erase existing events.
    by_id = {item["id"]: item for item in audit}
    for previous in initial_audit:
        assert previous["id"] in by_id and by_id[previous["id"]] == previous
    events = success(api.get("/api/events"))
    assert original_note not in json.dumps(events) and updated_note not in json.dumps(events)


def test_administration_is_separate_immutable_record_and_stock_updates_atomically(api, crew_id):
    medication = success(api.post("/api/medications", role="medical", json={
        "name": "Demo saline", "quantity": 10, "unit": "vials", "expiry_date": "2030-01-01",
        "storage": "demo cabinet", "low_stock_threshold": 2,
    }))
    medication_id = medication["id"]
    result = success(api.post(f"/api/medications/{medication_id}/administer", role="medical", json={"crew_id": crew_id, "quantity": 3, "note": "simulated administration"}))
    assert isinstance(result, dict)
    meds = success(api.get("/api/medications", role="medical"))
    current = next(row for row in meds if row["id"] == medication_id)
    assert current["quantity"] == 7
    administrations = success(api.get("/api/administrations", role="medical"))
    matching = [row for row in administrations if row.get("medication_id") == medication_id and row.get("crew_id") == crew_id]
    assert len(matching) == 1
    administration = matching[0]
    assert administration["quantity"] == 3 and administration.get("id")
    assert administration.get("created_at") and (administration.get("actor") or administration.get("actor_id") or administration.get("administered_by"))
    attempt = api.patch(f"/api/administrations/{administration['id']}", role="medical", json={"quantity": 1})
    assert attempt.status_code in (403, 404, 405), "Administrations must not be modifiable"
    after = success(api.get("/api/administrations", role="medical"))
    assert next(row for row in after if row["id"] == administration["id"]) == administration
    failure(api.post(f"/api/medications/{medication_id}/administer", role="medical", json={"crew_id": crew_id, "quantity": 999, "note": "invalid excess"}), (400, 409, 422))
    after_meds = success(api.get("/api/medications", role="medical"))
    assert next(row for row in after_meds if row["id"] == medication_id)["quantity"] == 7
    after_records = success(api.get("/api/administrations", role="medical"))
    assert after_records == after


def test_audit_rejects_ordinary_mutation_or_deletion(api):
    before = success(api.get("/api/audit"))
    if not before:
        success(api.patch("/api/settings", json={"location": "audit mutation check"}))
        before = success(api.get("/api/audit"))
    assert before
    item_id = before[0]["id"]
    for method in ("PATCH", "DELETE"):
        response = api.request(method, f"/api/audit/{item_id}", role="admin", json={"outcome": "altered"})
        assert response.status_code in (403, 404, 405), f"audit accepted {method}"
    assert success(api.get("/api/audit")) == before
