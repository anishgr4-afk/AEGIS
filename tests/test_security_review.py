"""Regression tests for the security review; every app uses a temporary DB."""
import importlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from conftest import PASSWORD


NEW_PASSWORD = "fictional reset orbital lantern 42"
ANOTHER_PASSWORD = "another fictional stellar garden 57"


def login(client, username, password=PASSWORD, **kwargs):
    return client.post("/api/auth/login", json={"username": username, "password": password}, **kwargs)


def auth(token):
    return {"Authorization": "Bearer " + token}


def token(client, username):
    response = login(client, username)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["token"]


def account_id(client, token_value):
    response = client.get("/api/auth/session", headers=auth(token_value))
    assert response.status_code == 200
    return response.get_json()["id"]


def test_global_burst_survives_restart_ignores_forwarded_headers_and_expires(app_factory, monkeypatch):
    app = app_factory()
    core = importlib.import_module("backend.core")
    assert core.LOGIN_BURST_LIMIT == 60  # Production budget, reduced only for this test.
    monkeypatch.setattr(core, "LOGIN_BURST_LIMIT", 4)
    client = app.test_client()
    # The same shared proxy can serve a legitimate user after several failures;
    # the separate generous global limit can still affect all users when full.
    for index in range(3):
        response = login(client, f"fictional-{index}", "wrong-password", environ_base={"REMOTE_ADDR": "10.0.0.1"},
                         headers={"X-Forwarded-For": f"198.51.100.{index + 1}"})
        assert response.status_code == 401
    assert login(client, "commander", environ_base={"REMOTE_ADDR": "10.0.0.1"}).status_code == 200
    blocked = login(client, "medical", environ_base={"REMOTE_ADDR": "203.0.113.1"},
                    headers={"X-Forwarded-For": "192.0.2.55", "X-Real-IP": "192.0.2.56"})
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= core.LOGIN_BURST_WINDOW_SECONDS
    app2 = app_factory()  # New worker/restart with the same isolated DB.
    core2 = importlib.import_module("backend.core")
    monkeypatch.setattr(core2, "LOGIN_BURST_LIMIT", 4)
    restarted = app2.test_client()
    assert login(restarted, "operator", headers={"Forwarded": "for=192.0.2.100"}).status_code == 429
    with sqlite3.connect(app_factory.db_path) as db:
        assert db.execute("SELECT attempts FROM auth_login_budget WHERE id=1").fetchone() == (4,)
        audit = json.dumps(db.execute("SELECT actor,action,entity_id,before_json,after_json FROM audit").fetchall())
        assert PASSWORD not in audit and "wrong-password" not in audit
        assert "198.51.100" not in audit and "192.0.2.55" not in audit
        db.execute("UPDATE auth_login_budget SET window_start=?", (time.time() - 61,))
    assert login(restarted, "operator").status_code == 200
    with sqlite3.connect(app_factory.db_path) as db:
        assert db.execute("SELECT attempts FROM auth_login_budget WHERE id=1").fetchone() == (1,)


def test_concurrent_distinct_attempts_are_reserved_before_hashing(app, app_factory, monkeypatch):
    module = importlib.import_module("backend.app")
    core = importlib.import_module("backend.core")
    monkeypatch.setattr(core, "LOGIN_BURST_LIMIT", 4)
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    seen = []

    def slow_hash(_hash, password):
        with lock:
            seen.append(password)
            if len(seen) == 4:
                entered.set()
        assert release.wait(10)
        return False

    monkeypatch.setattr(module, "check_password_hash", slow_hash)

    def attempt(index):
        with app.test_client() as client:
            return login(client, f"not-real-{index}", f"wrong-{index}",
                         headers={"X-Forwarded-For": f"192.0.2.{index}"}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(attempt, i) for i in range(8)]
        try:
            assert entered.wait(10), "admitted requests did not reach password verification"
            deadline = time.monotonic() + 10
            while sum(f.done() for f in futures) < 4 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert sum(f.done() for f in futures) == 4
            assert len(seen) == 4  # No extra scrypts were admitted while four are in flight.
        finally:
            release.set()
        statuses = [f.result(timeout=10) for f in futures]
    assert sorted(statuses) == [401] * 4 + [429] * 4
    with sqlite3.connect(app_factory.db_path) as db:
        assert db.execute("SELECT attempts FROM auth_login_budget WHERE id=1").fetchone() == (4,)


def test_concurrent_account_failures_recheck_limit_after_hashing(app, monkeypatch):
    module = importlib.import_module("backend.app")
    barrier = threading.Barrier(6, timeout=10)

    def wrong_hash(_hash, _password):
        barrier.wait()
        return False

    monkeypatch.setattr(module, "check_password_hash", wrong_hash)

    def attempt(index):
        with app.test_client() as client:
            return login(client, "operator", f"incorrect-{index}").status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(attempt, range(6)))
    assert sorted(statuses) == [401] * 5 + [429]


@pytest.mark.parametrize("submitted_password", [PASSWORD, "wrong-racing-password"])
def test_login_hash_is_outside_write_lock_and_reset_race_cannot_create_session_or_bucket(
        app, app_factory, monkeypatch, submitted_password):
    module = importlib.import_module("backend.app")
    client = app.test_client()
    admin = token(client, "admin")
    operator = token(client, "operator")
    operator_id = account_id(client, operator)
    entered, release = threading.Event(), threading.Event()
    original = module.check_password_hash

    def pause_after_verify(stored, submitted):
        result = original(stored, submitted)
        if submitted == submitted_password:
            entered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(module, "check_password_hash", pause_after_verify)

    def old_login():
        with app.test_client() as separate:
            return login(separate, "operator", submitted_password).status_code

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(old_login)
        try:
            assert entered.wait(10)
            # An unrelated writer can commit while the login is in scrypt.
            with sqlite3.connect(app_factory.db_path, timeout=1) as db:
                db.execute("INSERT INTO runtime_state(key,value) VALUES('test:unrelated-write','ok')")
            reset = client.patch("/api/users/" + operator_id, json={"password": NEW_PASSWORD}, headers=auth(admin))
            assert reset.status_code == 200, reset.get_data(as_text=True)
        finally:
            release.set()
        assert pending.result(timeout=10) == 401
    assert client.get("/api/auth/session", headers=auth(operator)).status_code == 401
    with sqlite3.connect(app_factory.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (operator_id,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM auth_failures").fetchone()[0] == 0
    assert login(client, "operator", NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize("mutation", ["deactivate", "rename", "account-throttle"])
def test_login_rechecks_active_identity_and_throttle_before_session(app, app_factory, monkeypatch, mutation):
    module = importlib.import_module("backend.app")
    core = importlib.import_module("backend.core")
    entered, release = threading.Event(), threading.Event()
    original = module.check_password_hash

    def paused(stored, submitted):
        result = original(stored, submitted)
        entered.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(module, "check_password_hash", paused)

    def attempt():
        with app.test_client() as client:
            return login(client, "operator").status_code

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(attempt)
        try:
            assert entered.wait(10)
            if mutation == "account-throttle":
                with app.app_context():
                    db = core.get_db()
                    db.execute("BEGIN IMMEDIATE")
                    for _ in range(core.LOGIN_FAILURE_LIMIT):
                        core.throttle_failure("operator")
                    db.commit()
            else:
                with sqlite3.connect(app_factory.db_path, timeout=1) as db:
                    if mutation == "deactivate":
                        db.execute("UPDATE users SET active=0 WHERE username='operator'")
                    else:
                        db.execute("UPDATE users SET username='renamed' WHERE username='operator'")
        finally:
            release.set()
        assert pending.result(timeout=10) == (429 if mutation == "account-throttle" else 401)
    with sqlite3.connect(app_factory.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_admin_reset_clears_only_target_account_and_unauthorized_or_invalid_cannot_clear(client, app_factory):
    admin = token(client, "admin")
    operator = token(client, "operator")
    operator_id = account_id(client, operator)
    for _ in range(5):
        assert login(client, "operator", "incorrect").status_code == 401
    assert login(client, "medical", "incorrect").status_code == 401
    assert login(client, "operator").status_code == 429
    path = "/api/users/" + operator_id
    assert client.patch(path, json={"password": NEW_PASSWORD}, headers=auth(operator)).status_code == 403
    assert client.patch(path, json={"password": "short"}, headers=auth(admin)).status_code == 400
    assert login(client, "operator").status_code == 429
    with sqlite3.connect(app_factory.db_path) as db:
        assert sorted(row[0] for row in db.execute("SELECT failures FROM auth_failures")) == [1, 5]
    assert client.patch(path, json={"password": NEW_PASSWORD}, headers=auth(admin)).status_code == 200
    with sqlite3.connect(app_factory.db_path) as db:
        assert [row[0] for row in db.execute("SELECT failures FROM auth_failures")] == [1]
        audit = json.dumps(db.execute("SELECT actor,action,entity_id,before_json,after_json FROM audit").fetchall())
        stored = db.execute("SELECT password_hash FROM users WHERE id=?", (operator_id,)).fetchone()[0]
        for secret in (PASSWORD, NEW_PASSWORD, stored, admin, operator):
            assert secret not in audit
    assert client.get("/api/auth/session", headers=auth(operator)).status_code == 401
    assert login(client, "operator", NEW_PASSWORD).status_code == 200


def test_reset_rollback_restores_credentials_sessions_and_throttle(client, app_factory, monkeypatch):
    ops = importlib.import_module("backend.ops")
    admin = token(client, "admin")
    operator = token(client, "operator")
    operator_id = account_id(client, operator)
    for _ in range(5):
        assert login(client, "operator", "incorrect").status_code == 401
    original_log = ops.log

    def fail_audit(action, *args, **kwargs):
        if action == "user_update":
            raise RuntimeError("simulated audit failure")
        return original_log(action, *args, **kwargs)

    monkeypatch.setattr(ops, "log", fail_audit)
    response = client.patch("/api/users/" + operator_id, json={"password": NEW_PASSWORD}, headers=auth(admin))
    assert response.status_code == 500
    assert client.get("/api/auth/session", headers=auth(operator)).status_code == 200
    assert login(client, "operator", NEW_PASSWORD).status_code == 429
    with sqlite3.connect(app_factory.db_path) as db:
        assert db.execute("SELECT failures FROM auth_failures").fetchone() == (5,)
        db.execute("UPDATE auth_failures SET window_start=?", (time.time() - 301,))
    assert login(client, "operator", PASSWORD).status_code == 200
    assert login(client, "operator", NEW_PASSWORD).status_code == 401


def test_reset_with_rename_clears_old_and_new_account_buckets_only(client, app_factory):
    admin = token(client, "admin")
    operator = token(client, "operator")
    operator_id = account_id(client, operator)
    for username in ("operator", "new-operator", "medical"):
        assert login(client, username, "incorrect").status_code == 401
    result = client.patch("/api/users/" + operator_id,
                          json={"username": "new-operator", "password": NEW_PASSWORD}, headers=auth(admin))
    assert result.status_code == 200
    with sqlite3.connect(app_factory.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM auth_failures").fetchone() == (1,)
    assert login(client, "new-operator", NEW_PASSWORD).status_code == 200


def test_current_password_uses_login_whitespace_normalization_but_new_policy_is_unchanged(client):
    # Legacy authentication strips both ends, including tabs/newlines.
    session = token(client, "operator")
    path = "/api/auth/password"
    headers = auth(session)
    for bad in (" " + NEW_PASSWORD, NEW_PASSWORD + " ", "\n" + NEW_PASSWORD):
        response = client.post(path, json={"current_password": " \t" + PASSWORD + "\n", "new_password": bad}, headers=headers)
        assert response.status_code == 400
    response = client.post(path, json={"current_password": " \t" + PASSWORD + "\n", "new_password": NEW_PASSWORD}, headers=headers)
    assert response.status_code == 200
    assert login(client, "operator", "  " + NEW_PASSWORD + "  ").status_code == 200


def test_preview_default_and_explicit_secure_frame_ancestor_allowlist(app, app_factory, monkeypatch):
    module = importlib.import_module("backend.app")
    default = app.test_client().get("/api/health")
    assert "frame-ancestors" not in default.headers["Content-Security-Policy"]
    assert "X-Frame-Options" not in default.headers
    monkeypatch.setenv("CHECKPOINT_FRAME_ANCESTORS", "https://preview.example https://control.example:8443 https://[2001:db8::1]")
    configured = module.create_app({"TESTING": True, "DATABASE": str(app_factory.db_path), "SIMULATION_ENABLED": False})
    for path in ("/", "/api/health", "/api/no-such-route"):
        response = configured.test_client().get(path)
        assert "frame-ancestors https://preview.example https://control.example:8443 https://[2001:db8::1]" in response.headers["Content-Security-Policy"]
        assert "X-Frame-Options" not in response.headers


def test_frame_ancestor_configuration_rejects_unsafe_or_malformed_inputs(app, app_factory):
    module = importlib.import_module("backend.app")
    bad_values = [
        "http://example.com", "'self'", "'none'", "*", "https://*.example.com",
        "https://example.com/path", "https://example.com/", "https://example.com?x=1",
        "https://example.com#x", "https://user@example.com", "https://example.com:0",
        "https://example.com:65536", "https://example.com:", "https://example.com; default-src *",
        "https://example.com\r\nX-Injected: yes", "https://example.com\thttps://other.example",
        "https://bad..example", "https://-bad.example", "https://999.1.1.1",
        "https://[::zz]", "https://[::1%25eth0]", "https://exämple.example", "https://good.example\\@evil.example",
        " ".join(["https://example.com"] * 33), None,
    ]
    for value in bad_values:
        with pytest.raises(ValueError):
            module.create_app({"TESTING": True, "DATABASE": str(app_factory.db_path),
                               "SIMULATION_ENABLED": False, "FRAME_ANCESTORS": value})
