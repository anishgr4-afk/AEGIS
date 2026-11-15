import uuid

import pytest

from conftest import API, failure, parse_utc, success


def test_valid_measurement_preserves_metadata_and_is_in_snapshot(api, telemetry):
    created = success(api.post("/api/telemetry", json=telemetry))
    assert isinstance(created, dict) and created.get("id")
    for field in ("crew_id", "metric", "value", "unit", "source"):
        assert created[field] == telemetry[field]
    assert parse_utc(created["timestamp"]) == parse_utc(telemetry["timestamp"])
    parse_utc(created["created_at"])
    parse_utc(created["updated_at"])
    rows = success(api.get("/api/telemetry"))
    assert isinstance(rows, list)
    assert any(row["id"] == created["id"] for row in rows)
    snap = success(api.get("/api/snapshot"))
    assert any(row["id"] == created["id"] for row in snap["measurements"])


@pytest.mark.parametrize("changes", [
    {"crew_id": "missing-crew-id"}, {"metric": "not_a_metric"},
    {"value": None}, {"value": "not numeric"}, {"value": True}, {"value": -12},
    {"timestamp": "not-a-timestamp"}, {"timestamp": "2026-01-01T00:00:00"},
    {"unit": ""}, {"source": ""},
])
def test_invalid_measurements_rejected_without_persistence(api, telemetry, changes):
    before = success(api.get("/api/telemetry"))
    failure(api.post("/api/telemetry", json={**telemetry, **changes}), (400, 404, 422))
    after = success(api.get("/api/telemetry"))
    assert len(after) == len(before), "Invalid input must not persist any measurements"


@pytest.mark.parametrize("missing", ["crew_id", "metric", "value"])
def test_measurement_requires_every_contract_field(api, telemetry, missing):
    del telemetry[missing]
    failure(api.post("/api/telemetry", json=telemetry))


@pytest.mark.parametrize("missing", ["unit", "source"])
def test_omitted_metadata_is_rejected_or_filled(api, telemetry, missing):
    del telemetry[missing]
    response = api.post("/api/telemetry", json=telemetry)
    if response.status_code in (400, 422):
        failure(response)
    else:
        assert success(response).get(missing), f"Server must supply {missing} when accepting its omission"


def test_omitted_timestamp_is_rejected_or_filled_with_utc(api, telemetry):
    del telemetry["timestamp"]
    response = api.post("/api/telemetry", json=telemetry)
    if response.status_code in (400, 422):
        failure(response)
    else:
        parse_utc(success(response)["timestamp"])


def test_duplicate_measurement_identity_is_not_stored_twice(api, telemetry):
    # The contract requires duplicate prevention but does not explicitly specify
    # client-ID semantics. This test models retrying an identical observation.
    telemetry["id"] = str(uuid.uuid4())
    first = success(api.post("/api/telemetry", json=telemetry))
    second = api.post("/api/telemetry", json=telemetry)
    if second.status_code in (400, 409, 422):
        failure(second, (400, 409, 422))
    else:
        assert success(second)["id"] == first["id"], "A retry should return the existing identity"
    rows = success(api.get("/api/telemetry"))
    matches = [r for r in rows if all(r.get(k) == telemetry[k] for k in ("crew_id", "metric", "value", "unit", "source")) and parse_utc(r["timestamp"]) == parse_utc(telemetry["timestamp"])]
    assert len(matches) == 1


def test_database_survives_app_restart(app_factory, api, telemetry):
    created = success(api.post("/api/telemetry", json=telemetry))
    restarted = app_factory()
    fresh = API(restarted.test_client())
    rows = success(fresh.get("/api/telemetry"))
    persisted = next(row for row in rows if row["id"] == created["id"])
    assert persisted["value"] == telemetry["value"]
    assert persisted["source"] == telemetry["source"]
    assert parse_utc(persisted["timestamp"]) == parse_utc(telemetry["timestamp"])


def test_reusing_measurement_id_with_different_content_is_rejected(api, telemetry):
    proposed = {**telemetry, "id": str(uuid.uuid4())}
    first = success(api.post("/api/telemetry", json=proposed))
    failure(api.post("/api/telemetry", json={**proposed, "value": 88}), (400, 409, 422))
    rows = success(api.get("/api/telemetry"))
    same_id = [row for row in rows if row["id"] == first["id"]]
    assert len(same_id) == 1 and same_id[0]["value"] == proposed["value"]


@pytest.mark.parametrize("metric,value,unit", [
    ("heart_rate", 72, "bpm"), ("bp_systolic", 120, "mmHg"), ("bp_diastolic", 80, "mmHg"),
    ("spo2", 98, "%"), ("respiratory_rate", 15, "breaths/min"), ("temperature", 36.8, "°C"),
    ("sleep", 7.5, "hours"), ("hydration", 60, "%"), ("weight", 70, "kg"), ("body_fat", 20, "%"),
])
def test_supported_metrics_are_stored_with_units(api, telemetry, metric, value, unit):
    payload = {**telemetry, "metric": metric, "value": value, "unit": unit}
    created = success(api.post("/api/telemetry", json=payload))
    assert created["metric"] == metric and created["value"] == value and created["unit"] == unit
    assert created["source"] == payload["source"]
