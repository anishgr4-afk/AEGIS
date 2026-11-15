"""Operations Desk integration tests. The host may register the blueprint itself."""
import importlib
import json
from datetime import datetime

import pytest

from conftest import API, failure, success


def make_app(app_factory):
    app = app_factory()
    # The parent application owns registration; make the tests independently runnable.
    if "workspace" not in app.blueprints:
        app.register_blueprint(importlib.import_module("backend.workspace").workspace)
    return app


@pytest.fixture
def app(app_factory):
    return make_app(app_factory)


def search(api, **filters):
    return success(api.post("/api/workspace/tasks/search", json=filters))


def create_task(api, **overrides):
    data = {"title": "Inspect simulated stowage", "description": "Review operational labels", "priority": "high", "status": "open"}
    data.update(overrides)
    return success(api.post("/api/workspace/tasks", json=data), (201,))


def test_task_crud_filters_status_and_optional_fields(api, crew_id):
    created = create_task(api, assigned_crew_id=crew_id, due_date="2032-02-29")
    assert created["id"] and created["simulated"] is True
    assert created["assigned_crew_id"] == crew_id and created["due_date"] == "2032-02-29"
    assert datetime.fromisoformat(created["created_at"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0
    path = f"/api/workspace/tasks/{created['id']}"
    assert success(api.get(path))["title"] == created["title"]
    second = create_task(api, title="ÉTANCHÉITÉ", description="CABIN panel", priority="low")
    assert [item["id"] for item in search(api, query="étanchéité")] == [second["id"]]
    assert [item["id"] for item in search(api, query="operational", status="open", priority="high", assigned_crew_id=crew_id)] == [created["id"]]
    assert not search(api, status="completed")
    updated = success(api.patch(path, json={"description": "Revised note", "status": "in-progress", "assigned_crew_id": None, "due_date": ""}))
    assert updated["status"] == "in-progress" and updated["assigned_crew_id"] is None and updated["due_date"] is None
    assert success(api.patch(path, json={"status": "completed"}))["status"] == "completed"
    assert success(api.patch(path, json={"status": "open"}))["status"] == "open"  # deliberate reopen
    assert success(api.delete(path) if hasattr(api, "delete") else api.request("DELETE", path))["deleted"] is True
    failure(api.get(path), (404,))
    failure(api.patch(path, json={"title": "Missing"}), (404,))
    failure(api.request("DELETE", path), (404,))
    assert [item["id"] for item in search(api)] == [second["id"]]


def test_handover_author_server_time_and_append_only(api):
    login = success(api.client.post("/api/auth/login", json={"username": "operator", "password": "checkpoint-demo"}))
    token, user = login["token"], login["user"]
    response = api.post("/api/workspace/handovers", token=token, json={"content": "Simulated pump inspection complete."})
    record = success(response, (201,))
    assert record["author_id"] == user["id"] and record["author"] == user["display_name"]
    assert record["simulated"] is True
    assert datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0
    assert success(api.get("/api/workspace/handovers"))[0]["content"] == record["content"]
    failure(api.post("/api/workspace/handovers", json={"content": "x", "author": "forged"}), (400,))
    assert api.patch(f"/api/workspace/handovers/{record['id']}", json={"content": "changed"}).status_code in (404, 405)
    assert api.request("DELETE", f"/api/workspace/handovers/{record['id']}").status_code in (404, 405)


@pytest.mark.parametrize("username", ["commander", "operator", "admin", "medical"])
def test_each_standard_role_can_read_and_write(api, username):
    task = success(api.post("/api/workspace/tasks", role=username, json={"title": "Simulated work"}), (201,))
    assert success(api.patch(f"/api/workspace/tasks/{task['id']}", role=username, json={"status": "completed"}))["status"] == "completed"
    assert success(api.get("/api/workspace/tasks", role=username))
    assert success(api.post("/api/workspace/handovers", role=username, json={"content": "Operational note"}), (201,))
    assert success(api.get("/api/workspace/handovers", role=username))
    assert success(api.request("DELETE", f"/api/workspace/tasks/{task['id']}", role=username))["deleted"]


def test_authentication_and_read_only_role(api, app):
    paths = [("GET", "/api/workspace/tasks", None), ("POST", "/api/workspace/tasks/search", {}),
             ("POST", "/api/workspace/tasks", {"title": "x"}), ("GET", "/api/workspace/tasks/not-found", None),
             ("PATCH", "/api/workspace/tasks/not-found", {"title": "x"}), ("DELETE", "/api/workspace/tasks/not-found", None),
             ("GET", "/api/workspace/handovers", None), ("POST", "/api/workspace/handovers", {"content": "x"})]
    for method, path, body in paths:
        failure(api.request(method, path, role=None, json=body), (401,))
    task = create_task(api)
    token = api.login("operator")
    core = importlib.import_module("backend.core")
    with app.app_context():
        db = core.get_db()
        db.execute("UPDATE users SET role='Observer' WHERE username='operator'")
        db.commit()
    assert success(api.get("/api/workspace/tasks", token=token))
    assert success(api.post("/api/workspace/tasks/search", token=token, json={}))
    assert success(api.get(f"/api/workspace/tasks/{task['id']}", token=token))
    assert success(api.get("/api/workspace/handovers", token=token)) == []
    for method, path, body in [("POST", "/api/workspace/tasks", {"title": "x"}),
                               ("PATCH", f"/api/workspace/tasks/{task['id']}", {"status": "completed"}),
                               ("DELETE", f"/api/workspace/tasks/{task['id']}", None),
                               ("POST", "/api/workspace/handovers", {"content": "x"})]:
        failure(api.request(method, path, token=token, json=body), (403,))


@pytest.mark.parametrize("payload,status", [
    ({}, 400), ({"title": "  "}, 400), ({"title": 1}, 400), ({"title": "X" * 201}, 400),
    ({"title": "ok", "description": "x" * 4001}, 400), ({"title": "ok", "priority": "critical"}, 400),
    ({"title": "ok", "status": "done"}, 400), ({"title": "ok", "status": True}, 400),
    ({"title": "ok", "due_date": "2031-02-29"}, 400), ({"title": "ok", "due_date": "20320101"}, 400),
    ({"title": "ok", "assigned_crew_id": "unknown"}, 404), ({"title": "ok", "created_by": "spoof"}, 400),
    ({"title": "ok\u0000bad"}, 400), ({"title": "x", "unexpected": "z" * 17000}, 413),
])
def test_reject_invalid_task_payloads(api, payload, status):
    failure(api.post("/api/workspace/tasks", json=payload), (status,))
    assert search(api) == []


def test_validation_on_other_endpoints(api):
    task = create_task(api)
    path = f"/api/workspace/tasks/{task['id']}"
    for patch in ({}, {"id": "spoof"}, {"title": ""}, {"description": ["invalid"]}, {"priority": "urgent!"}):
        failure(api.patch(path, json=patch), (400,))
    for body in ({}, {"content": " "}, {"content": 4}, {"content": "x" * 4001}, {"content": "x", "timestamp": "forged"}):
        failure(api.post("/api/workspace/handovers", json=body), (400,))
    for filters in ({"limit": 101}, {"limit": True}, {"offset": -1}, {"query": "x" * 201}, {"status": "closed"}, {"unknown": "x"}):
        failure(api.post("/api/workspace/tasks/search", json=filters), (400,))
    failure(api.get("/api/workspace/tasks?limit=1&limit=2"), (400,))
    failure(api.get("/api/workspace/handovers?offset=bad"), (400,))
    response = api.request("POST", "/api/workspace/tasks", data="[]", headers={"Content-Type": "application/json"})
    failure(response, (400,))
    nested = '{"title":' + '[' * 2000 + '0' + ']' * 2000 + '}'
    failure(api.request("POST", "/api/workspace/tasks", data=nested, headers={"Content-Type": "application/json"}), (400,))


def test_pagination_headers_and_bounded_pages(api):
    ids = {create_task(api, title=f"Task {index}")["id"] for index in range(5)}
    first_response = api.get("/api/workspace/tasks?limit=2&offset=0")
    first = success(first_response)
    assert first_response.headers["X-Has-More"] == "true" and len(first) == 2
    second = success(api.get("/api/workspace/tasks?limit=2&offset=2"))
    last_response = api.get("/api/workspace/tasks?limit=2&offset=4")
    last = success(last_response)
    assert last_response.headers["X-Has-More"] == "false" and len(last) == 1
    assert {item["id"] for item in first + second + last} == ids
    assert len(search(api, limit=2, offset=2)) == 2
    for index in range(3):
        success(api.post("/api/workspace/handovers", json={"content": f"Note {index}"}), (201,))
    response = api.get("/api/workspace/handovers?limit=2")
    assert len(success(response)) == 2 and response.headers["X-Has-More"] == "true"


def test_audit_events_exclude_free_text_and_chain_is_valid(api, app):
    title, note, handover = "TITLE_PRIVATE_MARKER", "DESCRIPTION_PRIVATE_MARKER", "HANDOVER_PRIVATE_MARKER"
    task = create_task(api, title=title, description=note)
    success(api.patch(f"/api/workspace/tasks/{task['id']}", json={"title": "SECOND_TITLE_PRIVATE_MARKER", "status": "completed"}))
    success(api.post("/api/workspace/handovers", json={"content": handover}), (201,))
    success(api.request("DELETE", f"/api/workspace/tasks/{task['id']}"))
    audits = success(api.get("/api/audit"))
    events = success(api.get("/api/events"))
    for marker in (title, note, handover, "SECOND_TITLE_PRIVATE_MARKER"):
        assert marker not in json.dumps(audits)
        assert marker not in json.dumps(events)
    actions = [row["action"] for row in audits if row["action"].startswith("workspace_")]
    assert actions == ["workspace_task_create", "workspace_task_update", "workspace_handover_create", "workspace_task_delete"]
    update = next(row for row in audits if row["action"] == "workspace_task_update")
    assert update["before"]["status"] == "open" and update["after"]["status"] == "completed"
    assert update["after"]["changed_fields"] == ["status", "title"]
    with app.app_context():
        assert importlib.import_module("backend.core").verify_audit()


def test_failed_event_rolls_back_record_and_audit(api, monkeypatch):
    module = importlib.import_module("backend.workspace")
    def fail(*_args, **_kwargs):
        raise RuntimeError("must not be logged")
    monkeypatch.setattr(module, "record_event", fail)
    failure(api.post("/api/workspace/tasks", json={"title": "not persisted"}), (500,))
    assert search(api) == []
    assert not [row for row in success(api.get("/api/audit")) if row["action"] == "workspace_task_create"]


def test_restart_persistence_and_offline(api, app_factory, monkeypatch):
    # No workspace operation opens a socket, including after app restart.
    import socket
    def fail_network(*_args, **_kwargs):
        raise AssertionError("unexpected network access")
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    task = create_task(api, status="in-progress")
    handover = success(api.post("/api/workspace/handovers", json={"content": "Next simulated shift"}), (201,))
    restarted = make_app(app_factory)
    restarted_api = API(restarted.test_client())
    assert success(restarted_api.get(f"/api/workspace/tasks/{task['id']}"))["status"] == "in-progress"
    assert success(restarted_api.get("/api/workspace/handovers"))[0]["id"] == handover["id"]
    assert success(restarted_api.patch(f"/api/workspace/tasks/{task['id']}", json={"status": "completed"}))["status"] == "completed"
