import hashlib
import importlib
import json
import sqlite3

import pytest

from conftest import success


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def test_audit_hash_chain_covers_each_entry_and_detects_tampering(api, app, app_factory):
    success(api.patch("/api/settings", json={"location": "Integrity test alpha"}))
    success(api.patch("/api/settings", json={"location": "Integrity test beta"}))
    rows = success(api.get("/api/audit"))
    assert len(rows) >= 3
    previous = "0" * 64
    for row in rows:
        entry = dict(row)
        digest = entry.pop("hash")
        assert entry["prev_hash"] == previous
        assert hashlib.sha256(canonical(entry).encode("utf-8")).hexdigest() == digest
        previous = digest

    # Ordinary SQL UPDATE must be blocked. Bypassing that guard models a privileged
    # actor changing the DB file directly; the chain must then fail verification.
    with sqlite3.connect(app_factory.db_path) as db:
        with pytest.raises(sqlite3.DatabaseError):
            db.execute("UPDATE audit SET action = 'tampered' WHERE id = ?", (rows[0]["id"],))
        db.rollback()
        db.execute("DROP TRIGGER audit_no_update")
        db.execute("UPDATE audit SET action = 'tampered' WHERE id = ?", (rows[0]["id"],))
        db.commit()
    core = importlib.import_module("backend.core")
    with app.app_context():
        assert core.verify_audit() is False, "Chain verification failed to detect modified historical audit entry"
    response = api.get("/api/audit")
    success(response)
    assert response.headers.get("X-Audit-Chain-Valid") == "false"
