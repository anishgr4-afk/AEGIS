"""Local persistence and shared helpers for the CHECKPOINT simulation.

All functions operate inside a Flask application context.  Record helpers do not
commit: the caller owns the transaction so data, events and audits are atomic.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g, has_request_context, jsonify, request
from werkzeug.security import generate_password_hash

ROLES = ("Commander", "Medical Officer", "Mission Operator", "Administrator")
MEDICAL_ROLES = ("Medical Officer", "Administrator")
SIMULATION_NOTICE = "SIMULATED DATA — unverified demonstration only; not a medical device or approved clinical guidance. Human review is required."

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
DEMO_PASSWORD = "checkpoint-demo"
LOGIN_FAILURE_LIMIT = 5
LOGIN_WINDOW_SECONDS = 300
# Generous shared burst budget, independent of usernames and client-supplied IP
# headers. It counts all well-formed login attempts, including successes, so
# concurrent requests reserve their expensive hash work before doing it. A burst
# can temporarily affect every user (including those behind a shared proxy); it
# is not a distributed DoS defense or a substitute for trusted edge limits.
LOGIN_BURST_LIMIT = 60
LOGIN_BURST_WINDOW_SECONDS = 60

# A small local denylist is not a comprehensive compromised-password check.
COMMON_PASSWORDS = frozenset({
    DEMO_PASSWORD, "password1234", "password12345", "password123456",
    "passwordpassword", "123456789012", "1234567890123", "1234567890123456",
    "qwertyuiop123", "qwerty123456", "letmeinletmein", "adminadmin123",
})


def password_policy_error(value):
    """Return a safe validation message, never the submitted passphrase."""
    if not isinstance(value, str) or not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH:
        return f"New password must be {PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_LENGTH} characters."
    if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        return "New password cannot begin or end with whitespace or contain control characters."
    if value.casefold() in COMMON_PASSWORDS or len(set(value.casefold())) == 1:
        return "Choose a less common passphrase; the public demo password is not allowed."
    return None


def throttle_key(username):
    """Opaque, installation-specific identifier; never store submitted usernames."""
    key = get_db().execute("SELECT value FROM runtime_state WHERE key='auth:throttle-key'").fetchone()["value"]
    normalized = username.strip().lower().encode("utf-8")
    return hmac.new(bytes.fromhex(key), normalized, hashlib.sha256).hexdigest()


def throttle_remaining(username):
    """Return seconds until this account's fixed-window lock expires, or zero."""
    db = get_db()
    current = time.time()
    db.execute("DELETE FROM auth_failures WHERE window_start<=?", (current - LOGIN_WINDOW_SECONDS,))
    row = db.execute("SELECT failures,window_start FROM auth_failures WHERE account_key=?", (throttle_key(username),)).fetchone()
    if row and row["failures"] >= LOGIN_FAILURE_LIMIT:
        return max(1, math.ceil(row["window_start"] + LOGIN_WINDOW_SECONDS - current))
    return 0


def throttle_failure(username):
    db = get_db()
    db.execute("""INSERT INTO auth_failures(account_key,failures,window_start) VALUES(?,1,?)
        ON CONFLICT(account_key) DO UPDATE SET failures=failures+1""", (throttle_key(username), time.time()))


def throttle_clear(username):
    get_db().execute("DELETE FROM auth_failures WHERE account_key=?", (throttle_key(username),))


def reserve_login_burst():
    """Reserve one global login attempt under BEGIN IMMEDIATE, or return retry seconds.

    Durable reservations bound admissions within each window, including
    simultaneous requests; they are not a hard in-flight concurrency limit.
    A crash or successful login does not refund one. Fixed windows can permit a
    double burst at a boundary, and system clock changes can affect expiry.
    """
    db = get_db()
    current = time.time()
    row = db.execute("SELECT attempts,window_start FROM auth_login_budget WHERE id=1").fetchone()
    if row and row["window_start"] > current - LOGIN_BURST_WINDOW_SECONDS:
        if row["attempts"] >= LOGIN_BURST_LIMIT:
            return max(1, math.ceil(row["window_start"] + LOGIN_BURST_WINDOW_SECONDS - current))
        db.execute("UPDATE auth_login_budget SET attempts=attempts+1 WHERE id=1")
    else:
        db.execute("""INSERT INTO auth_login_budget(id,attempts,window_start) VALUES(1,1,?)
            ON CONFLICT(id) DO UPDATE SET attempts=1,window_start=excluded.window_start""", (current,))
    return 0

# Units and plausible input bounds are validation bounds, not clinical limits.
MEASUREMENT_DEFS = {
    "heart_rate": ("bpm", 0, 300),
    "bp_systolic": ("mmHg", 0, 350),
    "bp_diastolic": ("mmHg", 0, 250),
    "spo2": ("%", 0, 100),
    "respiratory_rate": ("breaths/min", 0, 100),
    "temperature": ("°C", 20, 50),
    "sleep": ("hours", 0, 24),
    "hydration": ("%", 0, 100),
    "weight": ("kg", 0, 500),
    "body_fat": ("%", 0, 100),
}
ENVIRONMENT_DEFS = {
    "cabin_temperature": ("°C", -100, 150),
    "cabin_pressure": ("kPa", 0, 200),
    "oxygen": ("%", 0, 100),
    "co2": ("ppm", 0, 100000),
    "humidity": ("%", 0, 100),
    "air_quality": ("index", 0, 1000),
    "radiation": ("mSv/h", 0, 1000),
}
DEFAULT_THRESHOLDS = {
    "heart_rate": {"min": 55, "max": 110, "critical_min": 40, "critical_max": 140, "unit": "bpm"},
    "bp_systolic": {"min": 90, "max": 140, "critical_min": 70, "critical_max": 180, "unit": "mmHg"},
    "bp_diastolic": {"min": 55, "max": 90, "critical_min": 40, "critical_max": 120, "unit": "mmHg"},
    "spo2": {"min": 95, "max": 100, "critical_min": 90, "critical_max": 100, "unit": "%"},
    "respiratory_rate": {"min": 10, "max": 22, "critical_min": 6, "critical_max": 30, "unit": "breaths/min"},
    "temperature": {"min": 35.5, "max": 37.8, "critical_min": 34, "critical_max": 39.5, "unit": "°C"},
    "sleep": {"min": 5, "max": 10, "critical_min": 3, "critical_max": 14, "unit": "hours"},
    "hydration": {"min": 40, "max": 75, "critical_min": 30, "critical_max": 85, "unit": "%"},
    "weight": {"min": 40, "max": 140, "critical_min": 25, "critical_max": 200, "unit": "kg"},
    "body_fat": {"min": 5, "max": 35, "critical_min": 2, "critical_max": 50, "unit": "%"},
    "cabin_temperature": {"min": 18, "max": 27, "critical_min": 12, "critical_max": 32, "unit": "°C"},
    "cabin_pressure": {"min": 90, "max": 110, "critical_min": 75, "critical_max": 120, "unit": "kPa"},
    "oxygen": {"min": 19, "max": 23, "critical_min": 16, "critical_max": 26, "unit": "%"},
    "co2": {"min": 0, "max": 3000, "critical_min": 0, "critical_max": 5000, "unit": "ppm"},
    "humidity": {"min": 25, "max": 70, "critical_min": 10, "critical_max": 85, "unit": "%"},
    "air_quality": {"min": 0, "max": 100, "critical_min": 0, "critical_max": 200, "unit": "index"},
    "radiation": {"min": 0, "max": 0.1, "critical_min": 0, "critical_max": 0.25, "unit": "mSv/h"},
    "radiation_cumulative": {"min": 0, "max": 50, "critical_min": 0, "critical_max": 100, "unit": "mSv"},
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def uid():
    return str(uuid.uuid4())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def get_db():
    if "db" not in g:
        path = current_app.config["DATABASE"]
        if path != ":memory:" and not str(path).startswith("file:"):
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=20, uri=str(path).startswith("file:"))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        if path != ":memory:":
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        g.db = connection
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        if db.in_transaction:
            db.rollback()
        db.close()


def list_records(kind):
    rows = get_db().execute("SELECT id,data,created_at,updated_at FROM records WHERE kind=? ORDER BY created_at, id", (kind,)).fetchall()
    return [_unpack(row) for row in rows]


def _unpack(row):
    data = json.loads(row["data"])
    data.update(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"])
    return data


def get_record(kind, id):
    row = get_db().execute("SELECT id,data,created_at,updated_at FROM records WHERE kind=? AND id=?", (kind, str(id))).fetchone()
    return _unpack(row) if row else None


def insert_record(kind, data, record_id=None):
    if not isinstance(data, dict):
        raise ValueError("Record must be an object")
    rid = str(record_id or uid())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", rid):
        raise ValueError("Invalid record id")
    timestamp = now()
    clean = {k: v for k, v in data.items() if k not in ("id", "created_at", "updated_at")}
    get_db().execute("INSERT INTO records(kind,id,data,created_at,updated_at) VALUES(?,?,?,?,?)", (kind, rid, canonical(clean), timestamp, timestamp))
    return dict(clean, id=rid, created_at=timestamp, updated_at=timestamp)


def update_record(kind, id, patch):
    if kind in {"measurement", "radiation", "environment", "administration", "event", "audit"}:
        raise ValueError("This record type is append-only")
    if not isinstance(patch, dict):
        raise ValueError("Patch must be an object")
    old = get_record(kind, id)
    if old is None:
        return None
    merged = {k: v for k, v in old.items() if k not in ("id", "created_at", "updated_at")}
    merged.update({k: v for k, v in patch.items() if k not in ("id", "created_at", "updated_at")})
    timestamp = now()
    get_db().execute("UPDATE records SET data=?,updated_at=? WHERE kind=? AND id=?", (canonical(merged), timestamp, kind, str(id)))
    return dict(merged, id=old["id"], created_at=old["created_at"], updated_at=timestamp)


def record_event(category, entity, detail):
    # Protected free-text content is never copied to the general mission log.
    category = str(category)[:80]
    entity = str(entity or "system")[:160]
    protected = ("medical", "medication", "administration", "incident", "emergency", "message", "communication", "security", "user", "export")
    if any(word in (category + " " + entity).lower() for word in protected):
        detail = "Protected activity recorded; details withheld."
    else:
        detail = str(detail or "Activity recorded.")[:300]
    return insert_record("event", {"category": category, "entity": entity, "detail": detail, "timestamp": now(), "simulated": True})


_REDACT_KEYS = {"password", "password_hash", "token", "token_hash", "authorization", "secret", "baseline", "medical_history", "current_health", "note", "notes", "follow_up", "diagnosis", "treatment", "report", "body", "subject", "checklist", "reason", "medical_note"}


def _redact(value, key=""):
    k = str(key).lower()
    if k in _REDACT_KEYS or any(x in k for x in ("password", "token", "secret")):
        return "[REDACTED]" if value is not None else None
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return str(value)


def record_audit(action, entity, entity_id, before=None, after=None, outcome="success"):
    db = get_db()
    # Acquiring the write lock before reading the previous hash protects the chain
    # when multiple threads/processes write simultaneously.
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    prev = db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    actor = getattr(g, "audit_actor", None)
    if actor is None:
        user = current_user()
        actor = user["id"] if user else "system"
    payload = {"id": uid(), "timestamp": now(), "actor": str(actor)[:128], "action": str(action)[:120], "entity": str(entity)[:120], "entity_id": str(entity_id or "")[:128], "before": _redact(before), "after": _redact(after), "outcome": str(outcome)[:80], "prev_hash": prev["hash"] if prev else "0" * 64}
    digest = hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()
    db.execute("INSERT INTO audit(id,timestamp,actor,action,entity,entity_id,before_json,after_json,outcome,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (payload["id"], payload["timestamp"], payload["actor"], payload["action"], payload["entity"], payload["entity_id"], canonical(payload["before"]), canonical(payload["after"]), payload["outcome"], payload["prev_hash"], digest))
    return dict(payload, hash=digest)


def audit_records():
    rows = get_db().execute("SELECT * FROM audit ORDER BY seq").fetchall()
    result = []
    for r in rows:
        result.append({"id": r["id"], "timestamp": r["timestamp"], "actor": r["actor"], "action": r["action"], "entity": r["entity"], "entity_id": r["entity_id"], "before": json.loads(r["before_json"]), "after": json.loads(r["after_json"]), "outcome": r["outcome"], "prev_hash": r["prev_hash"], "hash": r["hash"]})
    return result


def verify_audit():
    previous = "0" * 64
    for row in audit_records():
        supplied = row.pop("hash")
        if row["prev_hash"] != previous or not secrets.compare_digest(hashlib.sha256(canonical(row).encode("utf-8")).hexdigest(), supplied):
            return False
        previous = supplied
    return True


def public_user(row):
    if row is None:
        return None
    return {key: row[key] for key in ("id", "username", "display_name", "role")}


def current_user():
    if hasattr(g, "_checkpoint_user"):
        return g._checkpoint_user
    g._checkpoint_user = None
    if not has_request_context():
        return None
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or len(header) > 512:
        return None
    token = header[7:]
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = get_db().execute("SELECT u.id,u.username,u.display_name,u.role,u.active FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1", (digest, now())).fetchone()
    if row is not None:
        g._checkpoint_user = public_user(row)
    return g._checkpoint_user


def require_roles(*roles):
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                # Security failures are separately auditable, without recording bearer tokens.
                if not get_db().in_transaction:
                    get_db().execute("BEGIN IMMEDIATE")
                record_audit("access_denied", "security", request.path[:120], outcome="unauthenticated")
                g._commit_security = True
                return jsonify(error="Authentication required"), 401
            if roles and user["role"] not in roles:
                if not get_db().in_transaction:
                    get_db().execute("BEGIN IMMEDIATE")
                record_audit("access_denied", "security", request.path[:120], outcome="forbidden")
                g._commit_security = True
                return jsonify(error="Insufficient permissions"), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorate


def _seed(db):
    users = [("commander", "Commander", "Commander"), ("medical", "Medical Officer", "Medical Officer"), ("operator", "Mission Operator", "Mission Operator"), ("admin", "Administrator", "Administrator")]
    for username, display, role in users:
        db.execute("INSERT INTO users(id,username,display_name,role,password_hash,active) VALUES(?,?,?,?,?,1)", (uid(), username, display, role, generate_password_hash("checkpoint-demo")))
    settings = {"mission_day": 184, "location": "Mars transit • simulated", "delay_minutes": 18.7, "earth_connected": True, "last_sync": None, "retention_days": 365, "stale_after_minutes": 35, "thresholds": DEFAULT_THRESHOLDS, "simulated": True, "notice": SIMULATION_NOTICE}
    insert_record("settings", settings, "mission")
    crew_data = [
        ("Mara Chen", "Commander", 0),
        ("Amara Okafor", "Flight Surgeon", 1),
        ("Elias Vega", "Mission Specialist", 2),
    ]
    bases = {"heart_rate": 72, "bp_systolic": 119, "bp_diastolic": 76, "spo2": 98, "respiratory_rate": 15, "temperature": 36.8, "sleep": 7.4, "hydration": 59, "weight": 70, "body_fat": 19}
    crew_ids = []
    for name, role, index in crew_data:
        c = insert_record("crew", {"name": name, "role": role, "status": "Nominal", "baseline": "Simulated baseline for demonstration only", "medical_history": "Simulated record; no real patient information", "current_health": "No verified clinical assessment", "last_telemetry": None, "simulated": True})
        crew_ids.append(c["id"])
        for step in range(13):
            stamp = (datetime.now(timezone.utc) - timedelta(minutes=(12 - step) * 30 + 2)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            for n, (metric, base) in enumerate(bases.items()):
                v = base + (index - 1) * (2 if metric in ("heart_rate", "weight") else 0.2) + ((step + n + index) % 5 - 2) * (0.1 if metric in ("temperature", "sleep") else 0.5)
                insert_record("measurement", {"crew_id": c["id"], "metric": metric, "value": round(v, 2), "unit": MEASUREMENT_DEFS[metric][0], "source": "simulated historical sensor", "timestamp": stamp, "simulated": True})
        for step in range(7):
            stamp = (datetime.now(timezone.utc) - timedelta(hours=6-step, minutes=2)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            insert_record("radiation", {"crew_id": c["id"], "value": round(0.015 + index * 0.002, 3), "unit": "mSv", "source": "simulated dosimeter", "timestamp": stamp, "event": "", "simulated": True})
    env_base = {"cabin_temperature": 21.6, "cabin_pressure": 101.2, "oxygen": 20.8, "co2": 1380, "humidity": 45, "air_quality": 27, "radiation": 0.015}
    for step in range(13):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=(12-step)*30 + 2)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        for metric, base in env_base.items():
            value = base + ((step % 5) - 2) * (20 if metric == "co2" else 0.001 if metric == "radiation" else 0.1)
            insert_record("environment", {"metric": metric, "value": round(value, 3), "unit": ENVIRONMENT_DEFS[metric][0], "source": "simulated historical sensor", "timestamp": stamp, "simulated": True})
    for index, quantity in enumerate((24, 8), start=1):
        insert_record("medication", {"name": f"Fictional demo inventory {index}", "quantity": quantity, "unit": "units", "expiry_date": (datetime.now(timezone.utc).date() + timedelta(days=365 + 90 * index)).isoformat(), "storage": "Simulated cabinet", "low_stock_threshold": 3, "created_by": "system", "simulated": True})
    record_event("system", "mission", "Simulated mission initialized; thresholds are unverified demonstration values.")
    record_audit("seed", "system", "mission", after={"simulated": True, "crew_count": len(crew_ids), "notice": SIMULATION_NOTICE})


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS records (
          kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(kind,id)
        );
        CREATE INDEX IF NOT EXISTS records_kind_created ON records(kind,created_at);
        CREATE INDEX IF NOT EXISTS records_kind_timestamp_v2 ON records(kind, substr(rtrim(json_extract(data,'$.timestamp'),'Z') || CASE WHEN instr(json_extract(data,'$.timestamp'),'.')>0 THEN '000000' ELSE '.000000' END,1,26));
        CREATE INDEX IF NOT EXISTS records_measurement_latest_v2 ON records(kind, json_extract(data,'$.crew_id'), json_extract(data,'$.metric'), substr(rtrim(json_extract(data,'$.timestamp'),'Z') || CASE WHEN instr(json_extract(data,'$.timestamp'),'.')>0 THEN '000000' ELSE '.000000' END,1,26) DESC);
        CREATE INDEX IF NOT EXISTS records_metric_latest_v2 ON records(kind, json_extract(data,'$.metric'), substr(rtrim(json_extract(data,'$.timestamp'),'Z') || CASE WHEN instr(json_extract(data,'$.timestamp'),'.')>0 THEN '000000' ELSE '.000000' END,1,26) DESC);
        CREATE INDEX IF NOT EXISTS records_alert_condition ON records(kind, json_extract(data,'$.condition_key'));
        CREATE INDEX IF NOT EXISTS records_alert_state ON records(kind, json_extract(data,'$.resolved'), json_extract(data,'$.severity'));
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
          display_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
        CREATE TABLE IF NOT EXISTS audit (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
          timestamp TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
          entity TEXT NOT NULL, entity_id TEXT NOT NULL, before_json TEXT NOT NULL,
          after_json TEXT NOT NULL, outcome TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN
          SELECT RAISE(ABORT, 'audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN
          SELECT RAISE(ABORT, 'audit is append-only'); END;
        CREATE TABLE IF NOT EXISTS runtime_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS auth_failures (
          account_key TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS auth_failures_window ON auth_failures(window_start);
        CREATE TABLE IF NOT EXISTS auth_login_budget (
          id INTEGER PRIMARY KEY CHECK(id=1), attempts INTEGER NOT NULL, window_start REAL NOT NULL
        );
    """)
    db.execute("BEGIN IMMEDIATE")
    db.execute("INSERT OR IGNORE INTO runtime_state(key,value) VALUES('auth:throttle-key',?)", (secrets.token_hex(32),))
    if db.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0 and db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        _seed(db)
    db.commit()
