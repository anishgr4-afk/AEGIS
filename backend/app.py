"""CHECKPOINT: offline-first, simulated mission API. Not a medical device."""
from __future__ import annotations

# Support both `python -m backend.app` and `python backend/app.py`.
import os
import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hashlib
import importlib
import ipaddress
import json
import math
import random
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import Flask, current_app, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

from backend.core import (
    DEFAULT_THRESHOLDS, ENVIRONMENT_DEFS, MEDICAL_ROLES, MEASUREMENT_DEFS,
    ROLES, SIMULATION_NOTICE, audit_records, close_db, current_user, get_db,
    get_record, init_db, insert_record, list_records, now, public_user,
    record_audit, record_event, require_roles, uid, update_record, verify_audit,
    DEMO_PASSWORD, PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH, password_policy_error,
    throttle_remaining, throttle_failure, throttle_clear, reserve_login_burst,
)

ROOT = Path(__file__).resolve().parent.parent
GUIDANCE = "Unverified demonstration rule only. Review the reading and context with an authorized human; no diagnosis or treatment is provided."

# A snapshot is a recent window, not an archive. The latest reading for every
# crew/metric and environment metric is included in addition to the window.
SNAPSHOT_RECENT = 1500
SNAPSHOT_LIMITS = {"measurements": 2500, "radiation": 1000, "environment": 1507, "alerts": 500, "events": 200}
HISTORY_LIMIT = 1000
RETENTION_NOTICE = "retention_days is metadata only; no automatic deletion or archival is implemented. All records remain in the local database."
RADIATION_NOTICE = "Mission total sums every crew-tagged and mission-tagged dose increment, without deduplication; it is not a physical fleet-wide dose estimate."
# Public dummy hash has the same default scrypt cost as seeded passwords. It is
# used for nonexistent accounts to reduce the most obvious timing distinction.
DUMMY_PASSWORD_HASH = "scrypt:32768:8:1$9KrZ7ARDxN620Xwd$957d629bcef34b4b1e1081cfbafff2825353826fcf721826fc5c97ec96eb2bf5c6d2f207852c984b6b978ae74851001a12c245195a8c8e7fc1da37e8d05117d7"


def validate_frame_ancestors(value):
    """Return explicit HTTPS origins only; reject invalid config at startup.

    No wildcard, CSP keyword, path, credentials, query, control character or
    header delimiter is accepted. Empty configuration leaves Preview embeddable.
    """
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("CHECKPOINT_FRAME_ANCESTORS must contain only HTTPS origins separated by spaces")
    origins = [item for item in value.split(" ") if item]
    if len(origins) > 32:
        raise ValueError("Too many frame ancestor origins")
    validated = []
    for origin in origins:
        match = re.fullmatch(r"https://(\[[0-9a-fA-F:.]+\]|[a-zA-Z0-9.-]+)(?::([0-9]{1,5}))?", origin, re.IGNORECASE)
        if not match:
            raise ValueError("Invalid HTTPS frame ancestor origin")
        host, port = match.groups()
        if port is not None and not 1 <= int(port) <= 65535:
            raise ValueError("Invalid frame ancestor port")
        try:
            if host.startswith("["):
                ipaddress.IPv6Address(host[1:-1])
            elif re.fullmatch(r"[0-9.]+", host):
                ipaddress.IPv4Address(host)
            elif len(host) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split(".")):
                raise ValueError("Invalid host")
        except ValueError:
            raise ValueError("Invalid frame ancestor host") from None
        validated.append("https://" + host.lower() + (":" + str(int(port)) if port is not None else ""))
    return " ".join(validated)


class APIError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def body(optional=False):
    data = request.get_json(silent=True)
    if optional and data is None and not request.data:
        return {}
    if not isinstance(data, dict):
        raise APIError("A JSON object is required")
    return data


def text(value, name, maximum=200, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise APIError(f"{name} must be text")
    value = value.strip()
    if required and not value:
        raise APIError(f"{name} is required")
    if len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise APIError(f"{name} is invalid or too long")
    return value


def number(value, name, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < lower or value > upper:
        raise APIError(f"{name} must be a finite number between {lower} and {upper}")
    return value


def timestamp(value=None):
    if value is None:
        return now()
    if not isinstance(value, str) or len(value) > 40:
        raise APIError("timestamp must be an ISO 8601 UTC timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        dt = dt.astimezone(timezone.utc)
    except ValueError:
        raise APIError("timestamp must be an ISO 8601 timestamp with timezone")
    if dt > datetime.now(timezone.utc) + timedelta(seconds=1):
        raise APIError("timestamp cannot be in the future")
    if dt.year < 2000:
        raise APIError("timestamp is outside the supported simulation range")
    return dt.isoformat().replace("+00:00", "Z")


def dt_parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def settings():
    result = get_record("settings", "mission")
    if result is None:
        raise APIError("Mission settings are unavailable", 503)
    # Also expose this on databases created before the limitation was documented.
    result["retention_notice"] = RETENTION_NOTICE
    result["radiation_total_notice"] = RADIATION_NOTICE
    result["message_scheduler_enabled"] = bool(current_app.config["SIMULATION_ENABLED"])
    return result


def _records_from_rows(rows):
    result = []
    for row in rows:
        data = json.loads(row["data"])
        data.update(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"])
        result.append(data)
    return result


def _order_field(kind):
    # All stored observations are UTC. Normalize optional fractional seconds
    # to six digits, so .000Z and whole-second Z compare equally, including at
    # inclusive filter boundaries. This expression matches the SQLite indexes.
    field = "detected_at" if kind == "alert" else "timestamp"
    value = f"json_extract(data,'$.{field}')"
    return f"substr(rtrim({value},'Z') || CASE WHEN instr({value},'.')>0 THEN '000000' ELSE '.000000' END,1,26)"


def _recent(kind, limit):
    order = _order_field(kind)
    rows = get_db().execute(f"SELECT id,data,created_at,updated_at FROM records WHERE kind=? ORDER BY {order} DESC, rowid DESC LIMIT ?", (kind, limit)).fetchall()
    return list(reversed(_records_from_rows(rows)))


def _latest_grouped(kind):
    order = _order_field(kind)
    db = get_db()
    if kind == "measurement":
        pairs = ((c["id"], metric) for c in list_records("crew") for metric in MEASUREMENT_DEFS)
        rows = [db.execute(f"""SELECT id,data,created_at,updated_at FROM records WHERE kind=?
             AND json_extract(data,'$.crew_id')=? AND json_extract(data,'$.metric')=?
             ORDER BY {order} DESC, rowid DESC LIMIT 1""", (kind, entity, metric)).fetchone()
                for entity, metric in pairs]
    else:
        rows = [db.execute(f"""SELECT id,data,created_at,updated_at FROM records WHERE kind=?
             AND json_extract(data,'$.metric')=? ORDER BY {order} DESC, rowid DESC LIMIT 1""", (kind, metric)).fetchone()
                for metric in ENVIRONMENT_DEFS]
    return _records_from_rows([row for row in rows if row is not None])


def _window(kind, limit, latest=None):
    # Newest first makes equal-timestamp latest-value selection deterministic.
    rows = list(reversed(_recent(kind, limit if latest is None else SNAPSHOT_RECENT)))
    if latest is not None:
        rows = list({r["id"]: r for r in rows + latest}.values())
    total = get_db().execute("SELECT COUNT(*) FROM records WHERE kind=?", (kind,)).fetchone()[0]
    return rows, {"limit": limit, "returned": len(rows), "total": total, "truncated": len(rows) < total}


def _query_history(kind):
    """Newest pages, each returned chronologically. Filters are inclusive UTC times."""
    allowed = {"limit", "offset", "since", "until"}
    filters = {"measurement": ("crew_id", "metric"), "radiation": ("crew_id",),
               "environment": ("metric",), "alert": ("entity_type", "entity_id", "metric", "severity", "resolved", "acknowledged"),
               "event": ("category", "entity")}[kind]
    allowed.update(filters)
    if set(request.args) - allowed:
        raise APIError("Unsupported history filter")
    def integer_arg(name, default, minimum, maximum):
        value = request.args.get(name, str(default))
        if not value.isascii() or not value.isdigit() or not minimum <= int(value) <= maximum:
            raise APIError(f"{name} must be an integer between {minimum} and {maximum}")
        return int(value)
    limit = integer_arg("limit", HISTORY_LIMIT, 1, HISTORY_LIMIT)
    offset = integer_arg("offset", 0, 0, 1000000000)
    clauses, params = ["kind=?"], [kind]
    for key in filters:
        value = request.args.get(key)
        if value is None:
            continue
        if key in ("resolved", "acknowledged"):
            if value.lower() not in ("true", "false"):
                raise APIError(f"{key} must be true or false")
            value = 1 if value.lower() == "true" else 0
        elif len(value) > 128 or not value:
            raise APIError(f"Invalid {key} filter")
        clauses.append(f"json_extract(data,'$.{key}')=?")
        params.append(value)
    order = _order_field(kind)
    bounds = {}
    for key, op in (("since", ">="), ("until", "<=")):
        value = request.args.get(key)
        if value is None:
            continue
        try:
            if len(value) > 40:
                raise ValueError()
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
            bounds[key] = parsed.astimezone(timezone.utc)
        except ValueError:
            raise APIError(f"{key} must be an ISO 8601 timestamp with timezone")
        clauses.append(f"{order} {op} ?")
        params.append(bounds[key].isoformat(timespec="microseconds").replace("+00:00", ""))
    if "since" in bounds and "until" in bounds and bounds["since"] > bounds["until"]:
        raise APIError("since must be earlier than or equal to until")
    where = " AND ".join(clauses)
    db = get_db()
    total = db.execute(f"SELECT COUNT(*) FROM records WHERE {where}", params).fetchone()[0]
    rows = db.execute(f"SELECT id,data,created_at,updated_at FROM records WHERE {where} ORDER BY {order} DESC, rowid DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    response = jsonify(list(reversed(_records_from_rows(rows))))
    response.headers.update({"X-Total-Count": str(total), "X-Limit": str(limit), "X-Offset": str(offset), "X-Has-More": str(offset + len(rows) < total).lower()})
    return response


def crew_view(record, measurements=None):
    result = dict(record)
    if current_user() and current_user()["role"] not in MEDICAL_ROLES:
        for key in ("baseline", "medical_history", "current_health", "diagnosis", "notes"):
            result.pop(key, None)
    if measurements is None:
        measurements = _latest_grouped("measurement")
    matching = [m for m in measurements if m.get("crew_id") == record["id"]]
    result["last_telemetry"] = max((m["timestamp"] for m in matching), key=dt_parse, default=None)
    result["freshness"] = freshness_map(matching, MEASUREMENT_DEFS, settings()["stale_after_minutes"])
    result["latest"] = latest_values(matching)
    return result


def latest_values(records):
    latest = {}
    for row in records:
        metric = row.get("metric")
        if metric and (metric not in latest or dt_parse(row["timestamp"]) > dt_parse(latest[metric]["timestamp"])):
            latest[metric] = row
    return latest


def freshness_map(records, definitions, stale_minutes):
    latest = latest_values(records)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    result = {}
    for metric in definitions:
        reading = latest.get(metric)
        is_stale = bool(reading and dt_parse(reading["timestamp"]) < cutoff)
        result[metric] = {"missing": reading is None, "stale": is_stale, "status": "missing" if reading is None else "stale" if is_stale else "fresh", "last_timestamp": reading.get("timestamp") if reading else None}
    return result


def new_alert(entity_type, entity_id, metric, value, unit, rule, severity, condition_key=None, observed_at=None, source_record_id=None):
    # An observation key is unique even when its value equals a previous one.
    # Freshness describes an ongoing state: deduplicate only while unresolved,
    # so manual resolution of a condition that persists re-arms on the next GET.
    if condition_key:
        sql = "SELECT id,data,created_at,updated_at FROM records WHERE kind='alert' AND json_extract(data,'$.condition_key')=?"
        if condition_key.startswith("freshness:"):
            sql += " AND json_extract(data,'$.resolved')=0"
        row = get_db().execute(sql + " LIMIT 1", (condition_key,)).fetchone()
        if row:
            return _records_from_rows([row])[0]
    data = {"entity_type": entity_type, "entity_id": entity_id, "metric": metric, "value": value, "unit": unit, "rule": rule, "severity": severity, "detected_at": now(), "observed_at": observed_at, "acknowledged": False, "resolved": False, "guidance": GUIDANCE, "simulated": True, "condition_key": condition_key, "source_record_id": source_record_id}
    alert = insert_record("alert", data)
    record_audit("alert_generated", "alert", alert["id"], after=alert)
    record_event("alert", entity_id, f"Simulated {severity.lower()} alert: {metric} ({rule}).")
    return alert


def evaluate(entity_type, entity_id, metric, value, unit, observed_at=None, occurrence=None, source_record_id=None):
    threshold = settings().get("thresholds", {}).get(metric)
    if not threshold or threshold.get("unit") != unit:
        return
    identity = json.dumps([occurrence, entity_type, entity_id, metric], separators=(",", ":"), ensure_ascii=False)
    key = f"threshold:{occurrence}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}" if occurrence else None
    if value < threshold["critical_min"]:
        return new_alert(entity_type, entity_id, metric, value, unit, "below critical_min", "CRITICAL", key, observed_at, source_record_id)
    if value > threshold["critical_max"]:
        return new_alert(entity_type, entity_id, metric, value, unit, "above critical_max", "CRITICAL", key, observed_at, source_record_id)
    if value < threshold["min"]:
        return new_alert(entity_type, entity_id, metric, value, unit, "below min", "WARNING", key, observed_at, source_record_id)
    if value > threshold["max"]:
        return new_alert(entity_type, entity_id, metric, value, unit, "above max", "WARNING", key, observed_at, source_record_id)


def detect_stale(crew, measurements, environment):
    stale_minutes = settings()["stale_after_minutes"]
    rows = get_db().execute("""SELECT id,data,created_at,updated_at FROM records WHERE kind='alert'
        AND json_extract(data,'$.resolved')=0 AND json_extract(data,'$.severity')='INFORMATIONAL'
        AND json_extract(data,'$.rule') IN ('missing','stale')""").fetchall()
    active_freshness_alerts = _records_from_rows(rows)

    def close_recovered(entity_type, entity_id, metric, keep_key=None):
        for alert in active_freshness_alerts:
            if alert.get("entity_type") == entity_type and alert.get("entity_id") == entity_id and alert.get("metric") == metric and (keep_key is None or alert.get("condition_key") != keep_key):
                reason = "The freshness condition was superseded by a newer simulated observation." if keep_key else "A fresh simulated observation was recorded."
                patch = {"resolved": True, "resolved_at": now(), "resolved_by": "system", "resolution_reason": reason}
                updated = update_record("alert", alert["id"], patch)
                record_audit("freshness_recovered", "alert", alert["id"], before=alert, after=updated)
                record_event("alert", entity_id, "An informational freshness alert was resolved after the observation changed.")

    for c in crew:
        matches = [m for m in measurements if m.get("crew_id") == c["id"]]
        for metric, state in freshness_map(matches, MEASUREMENT_DEFS, stale_minutes).items():
            if state["missing"] or state["stale"]:
                state_name = state["status"]
                key = f"freshness:crew:{c['id']}:{metric}:{state['last_timestamp'] or 'never'}"
                close_recovered("crew", c["id"], metric, key)
                new_alert("crew", c["id"], metric, None, MEASUREMENT_DEFS[metric][0], state_name, "INFORMATIONAL", key, state["last_timestamp"])
            else:
                close_recovered("crew", c["id"], metric)
    for metric, state in freshness_map(environment, ENVIRONMENT_DEFS, stale_minutes).items():
        if state["missing"] or state["stale"]:
            key = f"freshness:environment:mission:{metric}:{state['last_timestamp'] or 'never'}"
            close_recovered("environment", "mission", metric, key)
            new_alert("environment", "mission", metric, None, ENVIRONMENT_DEFS[metric][0], state["status"], "INFORMATIONAL", key, state["last_timestamp"])
        else:
            close_recovered("environment", "mission", metric)


def dose_totals(records):
    """Sum nonnegative increments exactly in decimal before conversion to JSON numbers.

    Mission total includes ALL crew and mission-tagged increments without
    deduplication; a mission-tagged subtotal is not the mission-wide total.
    """
    totals = {}
    today = datetime.now(timezone.utc).date().isoformat()
    daily = {}
    for r in records:
        entity = r["crew_id"]
        totals[entity] = totals.get(entity, Decimal(0)) + Decimal(str(r["value"]))
        if r["timestamp"][:10] == today:
            daily[entity] = daily.get(entity, Decimal(0)) + Decimal(str(r["value"]))
    return float(sum(totals.values(), Decimal(0))), {k: float(v) for k, v in totals.items()}, {k: float(v) for k, v in daily.items()}


def stored_dose_totals():
    rows = get_db().execute("SELECT json_extract(data,'$.crew_id') AS crew_id, json_extract(data,'$.value') AS value, json_extract(data,'$.timestamp') AS timestamp FROM records WHERE kind='radiation'")
    return dose_totals(rows)


def create_app(test_config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(
        DATABASE=os.environ.get("CHECKPOINT_DB_PATH") or os.environ.get("CHECKPOINT_DATABASE") or str(ROOT / "data" / "checkpoint.db"),
        MAX_CONTENT_LENGTH=128 * 1024,
        MAX_FORM_MEMORY_SIZE=128 * 1024,
        MAX_FORM_PARTS=50,
        SESSION_HOURS=8,
        SIMULATION_ENABLED=os.environ.get("CHECKPOINT_SIMULATION", "1").lower() not in ("0", "false", "off", "no"),
        SIMULATION_INTERVAL=float(os.environ.get("CHECKPOINT_SIMULATION_INTERVAL", "300")),
        MESSAGE_SCHEDULER_INTERVAL=float(os.environ.get("CHECKPOINT_MESSAGE_SCHEDULER_INTERVAL", "1")),
        EXPIRY_WARNING_DEFAULT=30,
        FRAME_ANCESTORS=os.environ.get("CHECKPOINT_FRAME_ANCESTORS", ""),
    )
    if test_config:
        app.config.update(test_config)
        if "SIMULATION_ENABLED" not in test_config and test_config.get("TESTING"):
            app.config["SIMULATION_ENABLED"] = False
    app.config["FRAME_ANCESTORS"] = validate_frame_ancestors(app.config["FRAME_ANCESTORS"])
    app.config["SIMULATION_INTERVAL"] = max(5, min(3600, float(app.config["SIMULATION_INTERVAL"])))
    app.config["MESSAGE_SCHEDULER_INTERVAL"] = max(0.05, min(60, float(app.config["MESSAGE_SCHEDULER_INTERVAL"])))
    if app.config["DATABASE"] == ":memory:":
        app.config["DATABASE"] = f"file:checkpoint-{uid()}?mode=memory&cache=shared"
        app.extensions["checkpoint_memory_keeper"] = sqlite3.connect(app.config["DATABASE"], uri=True, check_same_thread=False)
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
        # Fresh seeds and older databases use the same persisted default. The
        # lock makes concurrent worker startup/migration atomic and auditable.
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        mission = get_record("settings", "mission")
        if mission is not None and "expiry_warning_days" not in mission:
            update_record("settings", "mission", {"expiry_warning_days": app.config["EXPIRY_WARNING_DEFAULT"]})
            record_audit("settings_default_migration", "settings", "mission",
                         after={"expiry_warning_days": app.config["EXPIRY_WARNING_DEFAULT"], "simulated": True})
        db.commit()

    @app.before_request
    def begin_transaction():
        # Reject known oversized bodies even on routes that do not read them.
        if request.content_length is not None and request.content_length > app.config["MAX_CONTENT_LENGTH"]:
            raise RequestEntityTooLarge()
        # Serializing writes protects idempotency, alert deduplication, and the audit chain.
        if request.path.startswith("/api/") and request.path != "/api/auth/login" and (request.method in ("POST", "PATCH", "PUT", "DELETE") or request.path in ("/api/snapshot", "/api/alerts")):
            get_db().execute("BEGIN IMMEDIATE")

    @app.after_request
    def finish_transaction(response):
        db = g.get("db")
        if db is not None and db.in_transaction:
            if response.status_code < 400 or getattr(g, "_commit_security", False):
                db.commit()
            else:
                db.rollback()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        # Preview is cross-origin: X-Frame-Options is never sent and the default
        # omits frame-ancestors. Non-preview deployments may opt in to an exact
        # HTTPS origin allowlist; validate it once at startup, never from requests.
        csp = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
            "worker-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'"
        )
        if app.config["FRAME_ANCESTORS"]:
            csp += "; frame-ancestors " + app.config["FRAME_ANCESTORS"]
        response.headers["Content-Security-Policy"] = csp
        # Never infer HTTPS from a client-controlled forwarded header.
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(APIError)
    def api_error(exc):
        if exc.status == 403:
            db = get_db()
            if db.in_transaction:
                db.rollback()
            db.execute("BEGIN IMMEDIATE")
            record_audit("access_denied", "security", request.path[:120], outcome="forbidden")
            g._commit_security = True
        return jsonify(error=exc.message), exc.status

    @app.errorhandler(413)
    def too_large(_exc):
        return jsonify(error="Request body is too large"), 413

    @app.errorhandler(sqlite3.IntegrityError)
    def conflict(_exc):
        return jsonify(error="The record conflicts with existing data"), 409

    @app.errorhandler(Exception)
    def unexpected(exc):
        if isinstance(exc, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify(error=exc.description), exc.code
            return exc
        # Avoid logging exception text because it can contain submitted medical information.
        app.logger.error("Request failed (%s)", type(exc).__name__)
        return jsonify(error="Unable to complete the request"), 500

    @app.get("/api/health")
    def health():
        get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok", simulated=True, notice=SIMULATION_NOTICE)

    @app.post("/api/auth/login")
    def login():
        data = body()
        username, password = data.get("username"), data.get("password")
        # Preserve the existing login's whitespace normalization. Malformed
        # credentials and unknown or inactive users have the same failure text.
        if (not isinstance(username, str) or not isinstance(password, str) or
                len(username) > 64 or len(password) > 256 or
                any(0xD800 <= ord(c) <= 0xDFFF for c in username + password)):
            return jsonify(error="Invalid username or password"), 401
        username, password = username.strip().lower(), password.strip()
        if not username or not password:
            return jsonify(error="Invalid username or password"), 401
        def throttled(remaining):
            g.audit_actor = "system"
            record_audit("login_throttled", "security", "login", outcome="rate_limited")
            g._commit_security = True
            response = jsonify(error="Too many login attempts; try again later")
            response.headers["Retry-After"] = str(remaining)
            return response, 429

        # Reserve an installation-wide attempt before scrypt. Never trust even
        # REMOTE_ADDR as a distinct end user: a reverse proxy may be shared and
        # forwarded headers are client-spoofable. The separate account bucket
        # still applies across source addresses. The fixed global burst budget
        # can block legitimate users during abuse, and is not a full DoS defense.
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        remaining = throttle_remaining(username)
        if remaining:
            return throttled(remaining)
        remaining = reserve_login_burst()
        if remaining:
            return throttled(remaining)
        user = db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        db.commit()  # The durable reservation survives a crash or a failed hash.

        # Expensive verification must not hold SQLite's global writer lock.
        matched = check_password_hash(user["password_hash"] if user else DUMMY_PASSWORD_HASH, password)
        db.execute("BEGIN IMMEDIATE")
        remaining = throttle_remaining(username)
        if remaining:
            return throttled(remaining)
        latest = db.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        unchanged = ((user is None and latest is None) or
                     (user is not None and latest is not None and user["id"] == latest["id"] and
                      user["password_hash"] == latest["password_hash"] and user["active"] == latest["active"]))
        valid = bool(unchanged and latest and latest["active"] and matched)
        if not valid:
            # A stale verification must not recreate a bucket cleared by an
            # intervening reset or be applied to a newly created account.
            if unchanged:
                throttle_failure(username)
            g.audit_actor = "system"
            record_audit("login_failed", "security", "login", outcome="denied")
            g._commit_security = True
            return jsonify(error="Invalid username or password"), 401
        user = latest
        throttle_clear(username)
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (now(),))
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = (datetime.now(timezone.utc) + timedelta(hours=float(app.config["SESSION_HOURS"]))).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        get_db().execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)", (token_hash, user["id"], now(), expires))
        g.audit_actor = user["id"]
        record_audit("login", "security", user["id"])
        return jsonify(token=token, user=public_user(user), expires_at=expires)

    @app.get("/api/auth/session")
    @require_roles(*ROLES)
    def session():
        return jsonify(current_user())

    @app.get("/api/auth/security")
    @require_roles(*ROLES)
    def security_status():
        # Only the server-verified account is checked; no other account's
        # credential state or password hash is exposed.
        row = get_db().execute("SELECT password_hash FROM users WHERE id=?", (current_user()["id"],)).fetchone()
        return jsonify(demo_password_in_use=check_password_hash(row["password_hash"], DEMO_PASSWORD),
                       min_password_length=PASSWORD_MIN_LENGTH, max_password_length=PASSWORD_MAX_LENGTH)

    @app.post("/api/auth/password")
    @require_roles(*ROLES)
    def change_password():
        data = body()
        if set(data) != {"current_password", "new_password"}:
            raise APIError("Provide current_password and new_password only")
        current, new = data["current_password"], data["new_password"]
        if (not isinstance(current, str) or not current or len(current) > PASSWORD_MAX_LENGTH or
                any(0xD800 <= ord(c) <= 0xDFFF for c in current)):
            raise APIError("Current password is incorrect")
        # Legacy sign-in strips surrounding whitespace. Apply the same rule to
        # current credentials only; new passphrases still face the strict policy.
        current = current.strip()
        if not current:
            raise APIError("Current password is incorrect")
        user = current_user()
        remaining = throttle_remaining(user["username"])
        if remaining:
            record_audit("password_change_throttled", "security", user["id"], outcome="rate_limited")
            g._commit_security = True
            response = jsonify(error="Too many attempts; try again later")
            response.headers["Retry-After"] = str(remaining)
            return response, 429
        db = get_db()
        row = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not check_password_hash(row["password_hash"], current):
            throttle_failure(user["username"])
            record_audit("password_change_failed", "security", user["id"], outcome="denied")
            g._commit_security = True
            # A 401 would cause the shared frontend client to discard a valid session.
            return jsonify(error="Current password is incorrect"), 400
        error = password_policy_error(new)
        if error:
            raise APIError(error)
        if check_password_hash(row["password_hash"], new):
            raise APIError("New password must differ from the current password")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), user["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        throttle_clear(user["username"])
        # No request fields, hashes, or bearer values enter the audit chain.
        record_audit("password_changed", "security", user["id"])
        return jsonify(success=True, message="Password changed. All sessions were signed out; sign in again.")

    @app.post("/api/auth/logout")
    @require_roles(*ROLES)
    def logout():
        token = request.headers["Authorization"][7:]
        get_db().execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        record_audit("logout", "security", current_user()["id"])
        return jsonify(success=True)

    @app.get("/api/snapshot")
    @require_roles(*ROLES)
    def snapshot():
        crew = list_records("crew")
        latest_measurements = _latest_grouped("measurement")
        latest_environment = _latest_grouped("environment")
        detect_stale(crew, latest_measurements, latest_environment)
        config = settings()
        total, per_crew, daily = stored_dose_totals()
        mission = dict(config, radiation_total=total, radiation_by_crew=per_crew, radiation_daily=daily, radiation_unit="mSv")
        measurements, mw = _window("measurement", SNAPSHOT_LIMITS["measurements"], latest_measurements)
        environment, ew = _window("environment", SNAPSHOT_LIMITS["environment"], latest_environment)
        radiation, rw = _window("radiation", SNAPSHOT_LIMITS["radiation"])
        events, vw = _window("event", SNAPSHOT_LIMITS["events"])
        db = get_db()
        # Unacknowledged critical records take priority. If even these exceed the
        # bound, explicit counts and the alert history endpoint expose the overflow.
        alert_rows = db.execute("""SELECT id,data,created_at,updated_at FROM records WHERE kind='alert'
            ORDER BY CASE WHEN json_extract(data,'$.resolved')=0 AND json_extract(data,'$.severity')='CRITICAL' AND json_extract(data,'$.acknowledged')=0 THEN 0
                          WHEN json_extract(data,'$.resolved')=0 AND json_extract(data,'$.severity')='CRITICAL' THEN 1
                          WHEN json_extract(data,'$.resolved')=0 AND json_extract(data,'$.severity')='WARNING' THEN 2
                          WHEN json_extract(data,'$.resolved')=0 THEN 3 ELSE 4 END,
                     rtrim(json_extract(data,'$.detected_at'),'Z') DESC, rowid DESC LIMIT ?""", (SNAPSHOT_LIMITS["alerts"],)).fetchall()
        alerts = _records_from_rows(alert_rows)
        counts = db.execute("""SELECT COUNT(*) AS total,
            COUNT(*) FILTER (WHERE json_extract(data,'$.resolved')=0) AS active,
            COUNT(*) FILTER (WHERE json_extract(data,'$.resolved')=0 AND json_extract(data,'$.severity')='CRITICAL') AS active_critical,
            COUNT(*) FILTER (WHERE json_extract(data,'$.resolved')=0 AND json_extract(data,'$.severity')='CRITICAL' AND json_extract(data,'$.acknowledged')=0) AS unacknowledged_critical
            FROM records WHERE kind='alert'""").fetchone()
        aw = {"limit": SNAPSHOT_LIMITS["alerts"], "returned": len(alerts), "total": counts["total"], "truncated": len(alerts) < counts["total"], "active": counts["active"], "active_critical": counts["active_critical"], "unacknowledged_critical": counts["unacknowledged_critical"]}
        windows = {"measurements": mw, "radiation": rw, "environment": ew, "alerts": aw, "events": vw}
        for key, endpoint in (("measurements", "/api/telemetry"), ("radiation", "/api/radiation"), ("environment", "/api/environment"), ("alerts", "/api/alerts"), ("events", "/api/events")):
            windows[key]["endpoint"] = endpoint
        return jsonify(mission=mission, crew=[crew_view(c, latest_measurements) for c in crew], measurements=measurements, radiation=radiation, environment=environment, environment_freshness=freshness_map(latest_environment, ENVIRONMENT_DEFS, config["stale_after_minutes"]), alerts=alerts, events=events, settings=config, history_window=windows, notice=SIMULATION_NOTICE)

    @app.route("/api/crew", methods=["GET", "POST"])
    @require_roles(*ROLES)
    def crew_collection():
        if request.method == "GET":
            m = _latest_grouped("measurement")
            return jsonify([crew_view(c, m) for c in list_records("crew")])
        if current_user()["role"] not in ("Commander", "Medical Officer", "Administrator"):
            raise APIError("Insufficient permissions", 403)
        data = body()
        fields = {"name": text(data.get("name"), "name", 120), "role": text(data.get("role"), "role", 120), "status": text(data.get("status", "Nominal"), "status", 120), "last_telemetry": None, "simulated": True}
        for key in ("baseline", "medical_history", "current_health"):
            if key in data and current_user()["role"] not in MEDICAL_ROLES:
                raise APIError("Medical fields require a medical officer or administrator", 403)
            fields[key] = text(data.get(key, ""), key, 4000, required=False)
        if len(list_records("crew")) >= 100:
            raise APIError("The simulated crew limit is 100", 400)
        record = insert_record("crew", fields)
        record_audit("crew_create", "crew", record["id"], after=record)
        record_event("crew", record["id"], "Simulated crew profile created.")
        return jsonify(crew_view(record, [])), 201

    @app.patch("/api/crew/<record_id>")
    @require_roles("Commander", "Medical Officer", "Administrator")
    def crew_update(record_id):
        old = get_record("crew", record_id)
        if old is None:
            raise APIError("Crew member not found", 404)
        data = body()
        allowed = {"name", "role", "status", "baseline", "medical_history", "current_health"}
        if not data or set(data) - allowed:
            raise APIError("No editable fields supplied or unrecognized fields")
        patch = {}
        for key, value in data.items():
            if key in ("baseline", "medical_history", "current_health") and current_user()["role"] not in MEDICAL_ROLES:
                raise APIError("Medical fields require a medical officer or administrator", 403)
            patch[key] = text(value, key, 4000 if key in ("baseline", "medical_history", "current_health") else 120, required=key in ("name", "role", "status"))
        record = update_record("crew", record_id, patch)
        record_audit("crew_update", "crew", record_id, before=old, after=record)
        record_event("crew", record_id, "Simulated crew profile updated.")
        return jsonify(crew_view(record))

    def ingest(kind):
        if request.method == "GET":
            return _query_history(kind)
        data = body()
        record_id = data.get("id") or request.headers.get("Idempotency-Key")
        if record_id is not None:
            record_id = text(record_id, "id", 128)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", record_id):
                raise APIError("Invalid record id")
        existing = get_record(kind, record_id) if record_id else None
        definitions = MEASUREMENT_DEFS if kind == "measurement" else ENVIRONMENT_DEFS
        if kind == "radiation":
            entity = text(data.get("crew_id", "mission"), "crew_id", 128)
            if entity != "mission" and not get_record("crew", entity):
                raise APIError("Crew member not found", 404)
            values = {"crew_id": entity, "value": number(data.get("value"), "value", 0, 1000000), "unit": text(data.get("unit"), "unit", 30), "source": text(data.get("source"), "source", 120), "timestamp": timestamp(data.get("timestamp", existing.get("timestamp") if existing else None)), "event": text(data.get("event", ""), "event", 200, required=False), "simulated": True}
            if values["unit"] != "mSv":
                raise APIError("Radiation increments must use mSv")
        else:
            metric = text(data.get("metric"), "metric", 40)
            if metric not in definitions:
                raise APIError("Unsupported metric")
            unit, lo, hi = definitions[metric]
            supplied_unit = data.get("unit")
            aliases = {"BPM": "bpm", "C": "°C", "°c": "°C", "hrs": "hours", "h": "hours", "breaths/minute": "breaths/min"}
            supplied_unit = aliases.get(supplied_unit, supplied_unit) if isinstance(supplied_unit, str) else supplied_unit
            if supplied_unit != unit:
                raise APIError(f"The required unit for {metric} is {unit}")
            values = {"metric": metric, "value": number(data.get("value"), "value", lo, hi), "unit": unit, "source": text(data.get("source"), "source", 120), "timestamp": timestamp(data.get("timestamp", existing.get("timestamp") if existing else None)), "simulated": True}
            if kind == "measurement":
                entity = text(data.get("crew_id"), "crew_id", 128)
                if not get_record("crew", entity):
                    raise APIError("Crew member not found", 404)
                values["crew_id"] = entity
        if existing:
            comparison = {k: v for k, v in existing.items() if k not in ("id", "created_at", "updated_at")}
            if comparison != values:
                raise APIError("Record id already exists with different data", 409)
            return jsonify(existing), 200
        record = insert_record(kind, values, record_id)
        record_audit(f"{kind}_append", kind, record["id"], after=record)
        if kind == "radiation":
            total, individual, _ = stored_dose_totals()
            # Zero increments do not represent a new cumulative exposure.
            if values["value"] > 0:
                if entity != "mission":
                    evaluate("radiation", entity, "radiation_cumulative", individual.get(entity, 0), "mSv", values["timestamp"], record["id"], record["id"])
                evaluate("radiation", "mission", "radiation_cumulative", total, "mSv", values["timestamp"], record["id"], record["id"])
        else:
            evaluate("crew" if kind == "measurement" else "environment", entity if kind == "measurement" else "mission", metric, values["value"], values["unit"], values["timestamp"], record["id"], record["id"])
        return jsonify(record), 201

    @app.route("/api/telemetry", methods=["GET", "POST"])
    @require_roles(*ROLES)
    def telemetry():
        return ingest("measurement")

    @app.route("/api/radiation", methods=["GET", "POST"])
    @require_roles(*ROLES)
    def radiation_api():
        return ingest("radiation")

    @app.route("/api/environment", methods=["GET", "POST"])
    @require_roles(*ROLES)
    def environment_api():
        return ingest("environment")

    @app.get("/api/alerts")
    @require_roles(*ROLES)
    def alerts():
        detect_stale(list_records("crew"), _latest_grouped("measurement"), _latest_grouped("environment"))
        return _query_history("alert")

    @app.post("/api/alerts/<record_id>/<action>")
    @require_roles(*ROLES)
    def alert_action(record_id, action):
        if action not in ("acknowledge", "resolve"):
            raise APIError("Unknown alert action", 404)
        old = get_record("alert", record_id)
        if old is None:
            raise APIError("Alert not found", 404)
        if old["entity_type"] == "crew" and current_user()["role"] not in MEDICAL_ROLES:
            raise APIError("Crew alerts require a medical officer or administrator", 403)
        if old["entity_type"] != "crew" and current_user()["role"] not in ("Commander", "Mission Operator", "Administrator"):
            raise APIError("Operational alerts require an authorized operator", 403)
        if old["resolved"]:
            if action == "resolve":
                return jsonify(old)
            raise APIError("Resolved alerts cannot be acknowledged", 409)
        if action == "acknowledge" and old["acknowledged"]:
            return jsonify(old)
        patch = {"acknowledged": True, "acknowledged_at": now(), "acknowledged_by": current_user()["id"]} if action == "acknowledge" else {"resolved": True, "resolved_at": now(), "resolved_by": current_user()["id"]}
        updated = update_record("alert", record_id, patch)
        record_audit("alert_" + action, "alert", record_id, before=old, after=updated)
        record_event("alert", old["entity_id"], f"Simulated alert {action}d.")
        return jsonify(updated)

    @app.route("/api/settings", methods=["GET", "PATCH"])
    @require_roles(*ROLES)
    def settings_api():
        old = settings()
        if request.method == "GET":
            return jsonify(old)
        if current_user()["role"] not in ("Commander", "Administrator"):
            raise APIError("Insufficient permissions", 403)
        data = body()
        allowed = {"mission_day", "location", "delay_minutes", "earth_connected", "retention_days", "stale_after_minutes", "expiry_warning_days", "thresholds"}
        if not data or set(data) - allowed:
            raise APIError("No editable fields supplied or unrecognized fields")
        patch = {}
        if "mission_day" in data:
            n = number(data["mission_day"], "mission_day", 0, 100000)
            if int(n) != n:
                raise APIError("mission_day must be an integer")
            patch["mission_day"] = int(n)
        if "location" in data:
            patch["location"] = text(data["location"], "location", 160)
        if "delay_minutes" in data:
            patch["delay_minutes"] = number(data["delay_minutes"], "delay_minutes", 0, 5256000)
        if "earth_connected" in data:
            if not isinstance(data["earth_connected"], bool):
                raise APIError("earth_connected must be true or false")
            patch["earth_connected"] = data["earth_connected"]
        for k, lo, hi in (("retention_days", 1, 36500), ("stale_after_minutes", 1, 10080), ("expiry_warning_days", 0, 36500)):
            if k in data:
                n = number(data[k], k, lo, hi)
                if int(n) != n:
                    raise APIError(f"{k} must be an integer")
                patch[k] = int(n)
        if "thresholds" in data:
            if not isinstance(data["thresholds"], dict) or not data["thresholds"]:
                raise APIError("thresholds must be a nonempty object")
            thresholds = {k: dict(v) for k, v in old["thresholds"].items()}
            for metric, rule in data["thresholds"].items():
                if metric not in DEFAULT_THRESHOLDS or not isinstance(rule, dict) or set(rule) - {"min", "max", "critical_min", "critical_max", "unit"}:
                    raise APIError("Unsupported threshold or field")
                new_rule = dict(thresholds[metric])
                new_rule.update(rule)
                for k in ("min", "max", "critical_min", "critical_max"):
                    number(new_rule[k], k, -1000000, 1000000)
                if not new_rule["critical_min"] <= new_rule["min"] <= new_rule["max"] <= new_rule["critical_max"]:
                    raise APIError("Thresholds must satisfy critical_min <= min <= max <= critical_max")
                if new_rule["unit"] != DEFAULT_THRESHOLDS[metric]["unit"]:
                    raise APIError("Threshold unit must match the metric")
                thresholds[metric] = new_rule
            patch["thresholds"] = thresholds
        updated = update_record("settings", old["id"], patch)
        record_audit("settings_update", "settings", old["id"], before=old, after=updated)
        record_event("system", "mission", "Simulated mission settings updated; rule thresholds are unverified.")
        if "thresholds" in patch:
            changed = {metric for metric, rule in patch["thresholds"].items() if old["thresholds"].get(metric) != rule}
            revision = uid()
            for reading in _latest_grouped("measurement"):
                if reading["metric"] in changed:
                    evaluate("crew", reading["crew_id"], reading["metric"], reading["value"], reading["unit"], reading["timestamp"], revision, reading["id"])
            for reading in _latest_grouped("environment"):
                if reading["metric"] in changed:
                    evaluate("environment", "mission", reading["metric"], reading["value"], reading["unit"], reading["timestamp"], revision, reading["id"])
            if "radiation_cumulative" in changed:
                total, individual, _ = stored_dose_totals()
                for entity, value in individual.items():
                    if entity != "mission":
                        evaluate("radiation", entity, "radiation_cumulative", value, "mSv", occurrence=revision)
                if individual:
                    evaluate("radiation", "mission", "radiation_cumulative", total, "mSv", occurrence=revision)
        # The operations blueprint also synchronizes on a reconnection.
        updated["retention_notice"] = RETENTION_NOTICE
        updated["radiation_total_notice"] = RADIATION_NOTICE
        updated["message_scheduler_enabled"] = bool(app.config["SIMULATION_ENABLED"])
        updated["sync_required"] = old["earth_connected"] is False and updated["earth_connected"] is True
        return jsonify(updated)

    @app.get("/api/events")
    @require_roles(*ROLES)
    def events():
        return _query_history("event")

    @app.get("/api/audit")
    @require_roles("Administrator")
    def audit():
        response = jsonify(audit_records())
        response.headers["X-Audit-Chain-Valid"] = "true" if verify_audit() else "false"
        return response

    # Register independent modules if installed. A missing optional module is not a boot failure.
    for module_name, blueprint_name in (("backend.ops", "ops"), ("backend.evidence", "evidence"), ("backend.workspace", "workspace")):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        app.register_blueprint(getattr(module, blueprint_name))

    @app.get("/")
    @app.get("/<path:path>")
    def frontend(path="index.html"):
        if path.startswith("api/"):
            raise APIError("Endpoint not found", 404)
        dist = ROOT / "dist"
        target = dist / path
        if target.is_file():
            return send_from_directory(dist, path)
        if (dist / "index.html").is_file():
            return send_from_directory(dist, "index.html")
        return jsonify(error="Frontend not built; run npm run build"), 503

    if app.config["SIMULATION_ENABLED"]:
        start_simulator(app)
        if "ops" in app.blueprints:
            start_message_scheduler(app)
    return app


def simulate_once(app):
    """Generate one actual observation batch, never backfilling missed intervals."""
    with app.app_context():
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        moment = time.time()
        saved = db.execute("SELECT value FROM runtime_state WHERE key='next_simulation'").fetchone()
        if saved and float(saved["value"]) > moment:
            db.rollback()
            return False
        db.execute("INSERT INTO runtime_state(key,value) VALUES('next_simulation',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(moment + app.config["SIMULATION_INTERVAL"]),))
        g.audit_actor = "simulator"
        stamp = now()
        rng = random.SystemRandom()
        ids = []
        doses = {}
        defaults = {"heart_rate": 72, "bp_systolic": 119, "bp_diastolic": 76, "spo2": 98, "respiratory_rate": 15, "temperature": 36.8, "sleep": 7.4, "hydration": 59, "weight": 70, "body_fat": 19}
        for index, crew in enumerate(list_records("crew")):
            for metric, base in defaults.items():
                amplitude = 2 if metric in ("heart_rate", "bp_systolic", "bp_diastolic") else 0.15 if metric in ("temperature", "sleep") else 0.5
                v = round(base + (index % 3 - 1) * (2 if metric in ("heart_rate", "weight") else 0.2) + rng.uniform(-amplitude, amplitude), 2)
                record = insert_record("measurement", {"crew_id": crew["id"], "metric": metric, "value": v, "unit": MEASUREMENT_DEFS[metric][0], "source": "live simulated sensor", "timestamp": stamp, "simulated": True})
                ids.append(record["id"])
                evaluate("crew", crew["id"], metric, v, MEASUREMENT_DEFS[metric][0], stamp, record["id"], record["id"])
            dose = round(app.config["SIMULATION_INTERVAL"] / 60 * 0.00001, 7)
            r = insert_record("radiation", {"crew_id": crew["id"], "value": dose, "unit": "mSv", "source": "live simulated dosimeter", "timestamp": stamp, "event": "", "simulated": True})
            ids.append(r["id"])
            doses[crew["id"]] = r["id"]
        env = {"cabin_temperature": 21.6, "cabin_pressure": 101.2, "oxygen": 20.8, "co2": 1380, "humidity": 45, "air_quality": 27, "radiation": 0.015}
        for metric, base in env.items():
            amp = 30 if metric == "co2" else 0.002 if metric == "radiation" else 0.2
            v = round(base + rng.uniform(-amp, amp), 3)
            r = insert_record("environment", {"metric": metric, "value": v, "unit": ENVIRONMENT_DEFS[metric][0], "source": "live simulated sensor", "timestamp": stamp, "simulated": True})
            ids.append(r["id"])
            evaluate("environment", "mission", metric, v, ENVIRONMENT_DEFS[metric][0], stamp, r["id"], r["id"])
        total, individual, _ = stored_dose_totals()
        for entity, record_id in doses.items():
            evaluate("radiation", entity, "radiation_cumulative", individual[entity], "mSv", stamp, record_id, record_id)
        if doses:
            evaluate("radiation", "mission", "radiation_cumulative", total, "mSv", stamp, "batch:" + stamp)
        record_audit("simulation_batch", "telemetry", stamp, after={"record_ids": ids, "count": len(ids), "simulated": True})
        db.commit()
        return True


def start_simulator(app):
    def worker():
        # Delay the first generated observation, preserving the truthful seed timestamps.
        interval = app.config["SIMULATION_INTERVAL"]
        while not stop.wait(interval):
            try:
                simulate_once(app)
            except Exception as exc:
                # Do not expose readings or notes in logs.
                app.logger.error("Simulation batch failed (%s)", type(exc).__name__)
    stop = threading.Event()
    app.extensions["checkpoint_simulation_stop"] = stop
    thread = threading.Thread(target=worker, name="checkpoint-simulator", daemon=True)
    app.extensions["checkpoint_simulation_thread"] = thread
    thread.start()


def schedule_messages_once(app):
    """Process locally simulated traffic under a persisted, transactional lease.

    An idle or disconnected scheduler only reads. The lease and all state,
    event, and audit changes commit together; a crash rolls them all back.
    Request-driven processing uses the same SQLite write lock and state machine.
    """
    ops_module = importlib.import_module("backend.ops")
    with app.app_context():
        db = get_db()
        _, connected, _ = ops_module.link_settings()
        if not connected or not ops_module.message_work_due():
            return []
        db.execute("BEGIN IMMEDIATE")
        moment = time.time()
        saved = db.execute("SELECT value FROM runtime_state WHERE key='next_message_schedule'").fetchone()
        if saved and float(saved["value"]) > moment:
            db.rollback()
            return []
        # Recheck under the write lock: connection and message state can change
        # between the inexpensive preflight and obtaining the lock.
        _, connected, delay = ops_module.link_settings()
        if not connected or not ops_module.message_work_due():
            db.rollback()
            return []
        db.execute("INSERT INTO runtime_state(key,value) VALUES('next_message_schedule',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(moment + app.config["MESSAGE_SCHEDULER_INTERVAL"]),))
        g.audit_actor = "message-scheduler"
        changed = ops_module.process_messages(connected, delay)
        db.commit()
        return changed


def start_message_scheduler(app):
    stop = app.extensions["checkpoint_simulation_stop"]

    def worker():
        # Run once immediately on startup so overdue persisted traffic resumes.
        while not stop.is_set():
            try:
                schedule_messages_once(app)
            except Exception as exc:
                # Never put message contents or notes in logs.
                app.logger.error("Message scheduler failed (%s)", type(exc).__name__)
            if stop.wait(app.config["MESSAGE_SCHEDULER_INTERVAL"]):
                break

    thread = threading.Thread(target=worker, name="checkpoint-message-scheduler", daemon=True)
    app.extensions["checkpoint_message_scheduler_thread"] = thread
    thread.start()


app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False, use_reloader=False, threaded=True)
