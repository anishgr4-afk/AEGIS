"""Local, serial checks, not a load test or a production latency guarantee."""
import os
import time

from conftest import success


def test_repeated_measurement_processing_and_dashboard_response(api, telemetry):
    # Bounds are configurable for constrained CI. Defaults reflect NFR-001:
    # processing/evaluation <=0.5s, snapshot <=1s after the successful event,
    # and a standard dashboard view <=2s. No authentication cost is timed.
    api.login("admin")
    process_limit = float(os.getenv("CHECKPOINT_TEST_PROCESS_LIMIT_SECONDS", "0.5"))
    snapshot_limit = float(os.getenv("CHECKPOINT_TEST_SNAPSHOT_LIMIT_SECONDS", "1.0"))
    process_times, snapshot_times = [], []
    for index in range(5):
        data = {**telemetry, "value": 70 + index, "source": f"timing-sensor-{index}"}
        started = time.perf_counter()
        success(api.post("/api/telemetry", json=data))
        process_times.append(time.perf_counter() - started)
        started = time.perf_counter()
        snapshot = success(api.get("/api/snapshot"))
        snapshot_times.append(time.perf_counter() - started)
        assert any(row.get("source") == data["source"] for row in snapshot["measurements"])
    assert max(process_times) <= process_limit, f"measurement processing times (s): {process_times}"
    assert max(snapshot_times) <= snapshot_limit, f"snapshot times (s): {snapshot_times}"
