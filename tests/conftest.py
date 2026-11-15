"""Shared API fixtures. All persistent state must live in pytest's temporary directory."""
from __future__ import annotations

import importlib
import inspect
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASSWORD = "checkpoint-demo"
USERS = ("commander", "medical", "operator", "admin")


def wait_for_backend():
    """Avoid collection-time imports while the backend is being written concurrently."""
    needed = [ROOT / "backend" / name for name in ("core.py", "app.py", "ops.py")]
    timeout = float(os.environ.get("CHECKPOINT_TEST_WAIT", "120"))
    deadline = time.monotonic() + timeout
    while not all(path.is_file() and path.stat().st_size for path in needed):
        if time.monotonic() >= deadline:
            missing = [str(path) for path in needed if not path.is_file() or not path.stat().st_size]
            pytest.fail("Backend was not ready before timeout; missing: " + ", ".join(missing), pytrace=False)
        time.sleep(0.25)


def import_app():
    # Reload all three together after setting the DB env, including for restart tests.
    for name in list(sys.modules):
        if name == "backend" or name.startswith("backend."):
            del sys.modules[name]
    module = importlib.import_module("backend.app")
    if callable(getattr(module, "create_app", None)):
        factory = module.create_app
        if "test_config" in inspect.signature(factory).parameters:
            app = factory(test_config={"TESTING": True, "SIMULATION_ENABLED": False})
        else:
            app = factory()
    else:
        app = getattr(module, "app", None)
    assert app is not None and hasattr(app, "test_client"), "backend.app must expose app or create_app()"
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def app_factory(tmp_path, monkeypatch):
    wait_for_backend()
    db_path = tmp_path / "checkpoint-test.sqlite3"
    # Common aliases are set together; backend versions may choose any one of these.
    for name in ("CHECKPOINT_DB", "CHECKPOINT_DB_PATH", "CHECKPOINT_DATABASE", "DATABASE_PATH", "DATABASE", "DB_PATH", "SQLITE_PATH", "SQLITE_DB_PATH"):
        monkeypatch.setenv(name, str(db_path))
    monkeypatch.setenv("FLASK_ENV", "testing")
    # backend.app can instantiate a module-level app on import, before we
    # get to configure a factory-created app; disable its worker too.
    monkeypatch.setenv("CHECKPOINT_SIMULATION", "0")

    def make():
        app = import_app()
        assert db_path.exists(), f"Backend did not initialize the configured temporary DB: {db_path}"
        return app

    make.db_path = db_path
    return make


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def client(app):
    return app.test_client()


class API:
    def __init__(self, client):
        self.client = client
        self.tokens = {}

    def login(self, username):
        if username not in self.tokens:
            response = self.client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
            assert response.status_code == 200, f"login {username}: {response.status_code} {response.get_data(as_text=True)}"
            data = response.get_json()
            assert isinstance(data, dict) and data.get("token") and isinstance(data.get("user"), dict)
            self.tokens[username] = data["token"]
        return self.tokens[username]

    def request(self, method, path, *, role="admin", json=None, token=None, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if role is not None or token is not None:
            headers["Authorization"] = "Bearer " + (token if token is not None else self.login(role))
        if json is not None:
            kwargs["json"] = json
        return self.client.open(path, method=method, headers=headers, **kwargs)

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def patch(self, path, **kwargs):
        return self.request("PATCH", path, **kwargs)


def success(response, statuses=(200, 201)):
    assert response.status_code in statuses, f"{response.request.method} {response.request.path}: {response.status_code} {response.get_data(as_text=True)}"
    data = response.get_json(silent=True)
    assert data is not None, "API success response must be JSON"
    return data


def failure(response, statuses=(400, 422)):
    assert response.status_code in statuses, f"expected {statuses}, got {response.status_code}: {response.get_data(as_text=True)}"
    data = response.get_json(silent=True)
    assert isinstance(data, dict) and isinstance(data.get("error"), str) and data["error"], "Failure must have a clear JSON error"
    return data


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value):
    assert isinstance(value, str), f"Expected ISO UTC string, got {value!r}"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0, f"Not UTC: {value}"
    return parsed


@pytest.fixture
def api(client):
    return API(client)


@pytest.fixture
def crew_id(api):
    rows = success(api.get("/api/crew"))
    assert isinstance(rows, list) and rows, "The demo database must contain seeded crew"
    assert rows[0].get("id")
    return rows[0]["id"]


@pytest.fixture
def telemetry(crew_id):
    return {"crew_id": crew_id, "metric": "heart_rate", "value": 75, "unit": "bpm", "source": "pytest-sensor", "timestamp": utc_now()}
