import time
import uuid
import sys
from datetime import datetime, timezone

from conftest import API, failure, parse_utc, success


def messages_by_id(api):
    rows = success(api.get("/api/messages", role="operator"))
    assert isinstance(rows, list)
    return {row["id"]: row for row in rows}


def create_message(api, **overrides):
    payload = {"direction": "outgoing", "subject": "pytest status update", "body": "simulated mission message", "priority": "normal"}
    payload.update(overrides)
    return success(api.post("/api/messages", role="operator", json=payload))


def test_offline_queue_survives_restart_and_reconnect_does_not_deliver_early(api, app_factory, telemetry):
    settings = success(api.patch("/api/settings", json={"earth_connected": False, "delay_minutes": 18.7}))
    assert settings["earth_connected"] is False
    # Local monitoring and alert-independent persistence remain available offline.
    measurement = success(api.post("/api/telemetry", json=telemetry))
    message = create_message(api)
    assert message["status"].lower() == "queued"
    assert not message.get("delivered_at") and not message.get("transmitted_at")
    failure(api.post("/api/sync", role="operator", json={}), (409, 503))
    assert messages_by_id(api)[message["id"]]["status"].lower() == "queued"

    restarted = API(app_factory().test_client())
    assert messages_by_id(restarted)[message["id"]]["status"].lower() == "queued"
    assert any(row["id"] == measurement["id"] for row in success(restarted.get("/api/telemetry")))
    success(restarted.patch("/api/settings", json={"earth_connected": True}))
    sync = success(restarted.post("/api/sync", role="operator", json={}))
    assert isinstance(sync, dict) and sync.get("last_sync")
    row = messages_by_id(restarted)[message["id"]]
    assert row["status"].lower() == "transmitted", "reconnection should transmit, not prematurely deliver"
    assert not row.get("delivered_at")
    transmitted, due = parse_utc(row["transmitted_at"]), parse_utc(row["due_at"])
    assert (due - transmitted).total_seconds() == pytest_approx(18.7 * 60, abs=0.01)
    assert datetime.now(timezone.utc) < due
    failure(restarted.post(f"/api/messages/{message['id']}/acknowledge", role="operator"), (400, 409, 422))
    settings_after = success(restarted.get("/api/settings"))
    assert parse_utc(settings_after["last_sync"]) == parse_utc(sync["last_sync"])
    for _ in range(2):
        success(restarted.post("/api/sync", role="operator", json={}))
    rows = success(restarted.get("/api/messages", role="operator"))
    assert sum(row["id"] == message["id"] for row in rows) == 1
    assert next(row for row in rows if row["id"] == message["id"])["status"].lower() == "transmitted"


def pytest_approx(value, **kwargs):
    import pytest
    return pytest.approx(value, **kwargs)


def test_sync_retries_are_deduplicated_and_conflicts_reported_explicitly(api, monkeypatch):
    # The operations API accepts an optional messages array; the base contract
    # does not prescribe its request shape, so this is an implementation adapter.
    ident = "pytest-" + str(uuid.uuid4())
    item = {"id": ident, "direction": "incoming", "subject": "sync test", "body": "original", "priority": "normal"}
    first = success(api.post("/api/sync", role="operator", json={"messages": [item]}))
    assert ident in first.get("added", [])
    repeated = success(api.post("/api/sync", role="operator", json={"messages": [item]}))
    assert ident in repeated.get("duplicates", [])
    conflicted = success(api.post("/api/sync", role="operator", json={"messages": [{**item, "body": "different"}]}))
    assert any(c.get("id") == ident for c in conflicted.get("conflicts", [])), "Same ID / different content must be reported"
    rows = success(api.get("/api/messages", role="operator"))
    assert len([row for row in rows if row["id"] == ident]) == 1
    pending = next(row for row in rows if row["id"] == ident)
    assert pending["content_pending"] and "body" not in pending
    # Advance the simulated communication clock, not the actual wall clock.
    monkeypatch.setattr(sys.modules["backend.ops"], "now", lambda: pending["due_at"])
    delivered = next(row for row in success(api.get("/api/messages", role="operator")) if row["id"] == ident)
    assert delivered["status"] == "delivered" and delivered["body"] == "original"


def test_failure_retry_delivery_and_acknowledgment_state_machine(api):
    success(api.patch("/api/settings", json={"earth_connected": True, "delay_minutes": 0.025}))
    message = create_message(api)
    ident = message["id"]
    assert message["status"].lower() == "transmitted"
    failure(api.post(f"/api/messages/{ident}/acknowledge", role="operator"), (400, 409, 422))
    failed = success(api.post(f"/api/messages/{ident}/fail", role="operator", json={"reason": "simulated failure"}))
    assert failed["status"].lower() == "failed"
    retried = success(api.post(f"/api/messages/{ident}/retry", role="operator"))
    assert retried["status"].lower() == "transmitted" and not retried.get("delivered_at")
    due = parse_utc(retried["due_at"])
    remaining = (due - datetime.now(timezone.utc)).total_seconds()
    assert remaining < 5, "test requires a short configured delay"
    if remaining > 0:
        time.sleep(remaining + 0.1)
    success(api.post("/api/sync", role="operator", json={}))
    delivered = messages_by_id(api)[ident]
    assert delivered["status"].lower() == "delivered"
    assert parse_utc(delivered["delivered_at"]) >= parse_utc(delivered["due_at"])
    ack = success(api.post(f"/api/messages/{ident}/acknowledge", role="operator"))
    assert ack["status"].lower() == "acknowledged" and ack.get("acknowledged_at")
