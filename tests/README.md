# CHECKPOINT backend tests

Run from the repository root with `python3 -m pytest tests -q` after installing `requirements.txt`. Tests use a different temporary SQLite database per test, the Flask test client, and the four demo accounts (password `checkpoint-demo`). The simulator worker is disabled to keep tests deterministic. No images, network traffic or production data are used. Fixtures wait up to 120 seconds for `backend/core.py`, `backend/app.py`, and `backend/ops.py` before importing; set `CHECKPOINT_TEST_WAIT` to change this timeout. The fixture reloads the backend against a temporary database for restart checks and asserts that the configured file is actually created.

`test_timing.py` performs five sequential in-process requests, not a load or browser benchmark. Its 0.5-second measurement and 1-second dashboard snapshot limits reflect the SRS under normal local conditions; in unusually slow CI, set `CHECKPOINT_TEST_PROCESS_LIMIT_SECONDS` or `CHECKPOINT_TEST_SNAPSHOT_LIMIT_SECONDS` and report that deviation. This does not prove memory, concurrency, or network performance.

## Contract interpretation and adapters

- For a retried identical telemetry observation, duplicate prevention is tested with the same proposed client `id` and identical measurement data. The contract requires avoiding duplicates but does not explicitly specify telemetry client-ID/idempotency semantics; a failure warrants a contract decision, since genuinely independent observations could have identical values.
- `/api/sync` does not specify a request schema. The conflict test uses the implementation's optional `messages` array and checks its `added`, `duplicates` and `conflicts` response fields. The ordinary empty-body sync/reconnection checks do not depend on that adapter.
- Omitted timestamp, source or unit metadata may be rejected or populated by the server; a resulting timestamp must be UTC and other metadata nonempty. Explicit source/unit/timestamp metadata must be preserved.
- The audit-chain test independently computes SHA-256 over the implementation's canonical JSON fields, checks the previous-hash links, and simulates privileged on-disk tampering in the isolated test DB after first checking the SQL update guard. This models detection, not cryptographic protection against an attacker able to rewrite the entire chain.
- Response-time checks use wall-clock bounds and can be noisy on contended hosts. Delayed-message tests use the real clock and a short simulated delay without faking delivery time.
- Medical and audit history may use implementation-specific structures; tests check their observable behavior without assuming an uncontracted single-record endpoint.
