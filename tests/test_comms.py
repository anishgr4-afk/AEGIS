"""Comms delay is derived from Earth distance at the speed of light."""
import importlib

import pytest

from comms import DEFAULT_DISTANCE_KM, SPEED_OF_LIGHT_KM_S, comms_fields, one_way_delay_s


def test_one_light_second():
    assert one_way_delay_s(SPEED_OF_LIGHT_KM_S) == 1.0


def test_moon_is_about_1_3_seconds():
    assert one_way_delay_s(DEFAULT_DISTANCE_KM["LUNAR_SURFACE"]) == 1.3


def test_mars_default_is_12_5_minutes_one_way():
    fields = comms_fields(DEFAULT_DISTANCE_KM["MARS_TRANSIT"])
    assert fields["comms_delay_seconds"] == pytest.approx(750.5)
    assert fields["min_round_trip_seconds"] == pytest.approx(1501.0)
    assert fields["earth_distance_km"] == 225_000_000.0


@pytest.fixture()
def client(monkeypatch):
    app_module = importlib.import_module("app")
    # Never write the real data files from tests.
    monkeypatch.setattr(app_module, "persist", lambda: None)
    snapshot = dict(app_module.active_day())
    env, dist = app_module.active_environment, app_module.active_distance_km
    yield app_module.app.test_client()
    app_module.active_day().clear()
    app_module.active_day().update(snapshot)
    app_module.active_environment, app_module.active_distance_km = env, dist


def test_environment_uses_default_distance(client):
    body = client.post("/api/environment", json={"environment": "LUNAR_SURFACE"}).get_json()
    assert body["earth_distance_km"] == 384_400.0
    assert body["comms_delay_seconds"] == 1.3
    assert body["day"]["comms_delay_seconds"] == 1.3


def test_environment_distance_override(client):
    # Mars at closest approach, ~54.6 million km -> ~3 min one way.
    body = client.post("/api/environment",
                       json={"environment": "MARS_TRANSIT", "earth_distance_km": 54_600_000}).get_json()
    assert body["comms_delay_seconds"] == pytest.approx(182.1)
    assert body["min_round_trip_seconds"] == pytest.approx(364.2)


@pytest.mark.parametrize("bad", [-1, "far", True, 5e9, None])
def test_environment_rejects_bad_distance(client, bad):
    resp = client.post("/api/environment", json={"environment": "MARS_TRANSIT", "earth_distance_km": bad})
    assert resp.status_code == 400
