"""CHECKPOINT operational API. All communications and checklists are simulations.

This module deliberately shares the core record store and transaction connection. It
never calls a remote Earth service or supplies clinical instructions.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import textwrap
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import wraps

from flask import Blueprint, Response, current_app, g, jsonify, request
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from werkzeug.security import check_password_hash, generate_password_hash

from .core import (
    get_db, now, uid, list_records, get_record, insert_record, update_record,
    record_event, record_audit, current_user, require_roles, password_policy_error, throttle_clear,
)

ops = Blueprint("ops", __name__)
MEDICAL = ("Medical Officer", "Administrator")
ALL_ROLES = ("Commander", "Medical Officer", "Mission Operator", "Administrator")
ROLES = set(ALL_ROLES)
DISCLAIMER = "DEMONSTRATION ONLY — not a medical device or approved clinical guidance. Human review required."


class APIError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status


def safe(f):
    """Keep transaction failures and malformed inputs from becoming HTML errors."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except APIError as exc:
            get_db().rollback()
            return jsonify(error=exc.message), exc.status
        except sqlite3.IntegrityError:
            get_db().rollback()
            return jsonify(error="A record with that identifier or username already exists."), 409
        except (sqlite3.Error, ValueError, TypeError) as exc:
            get_db().rollback()
            # Do not place input values, medical notes, or SQL in application logs.
            if isinstance(exc, sqlite3.Error):
                return jsonify(error="The operation could not be stored."), 500
            return jsonify(error="Invalid request data."), 400
        except Exception:
            get_db().rollback()
            return jsonify(error="The operation could not be completed."), 500
    return wrapper


def body(required=True):
    data = request.get_json(silent=True)
    if data is None and not required and not request.data:
        return {}
    if not isinstance(data, dict):
        raise APIError("A JSON object is required.")
    return data


def begin():
    db = get_db()
    # BEGIN IMMEDIATE serializes stock checks and message-ID deduplication. Core
    # helpers must not commit independently; all writes share this connection.
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    return db


def commit():
    get_db().commit()


def actor():
    user = current_user() or {}
    return user.get("id")


def actor_name():
    user = current_user() or {}
    return user.get("display_name") or user.get("username") or user.get("id")


def idempotency_lookup(data):
    """Called under the write lock; keys are scoped to the authenticated actor.

    The opaque digest identifies the complete payload and route without retaining
    extra copies of notes or message bodies. No key preserves legacy behavior.
    """
    header = request.headers.get("Idempotency-Key")
    supplied = data.get("idempotency_key")
    if header and supplied and header != supplied:
        raise APIError("Conflicting idempotency keys.")
    key = header or supplied
    if key is None:
        return None, None, None
    try:
        key = str(uuid.UUID(key))
    except (ValueError, TypeError, AttributeError):
        raise APIError("Idempotency key must be a UUID.")
    fingerprint = hashlib.sha256(json.dumps({"path": request.path, "body": {k: v for k, v in data.items() if k != "idempotency_key"}}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    ident = hashlib.sha256((str(actor()) + ":" + key).encode()).hexdigest()
    previous = get_record("ops_idempotency", ident)
    if previous and previous["fingerprint"] != fingerprint:
        raise APIError("Idempotency key already used for a different operation or payload.", 409)
    return ident, fingerprint, previous


def idempotency_store(ident, fingerprint, response, status):
    if ident:
        insert_record("ops_idempotency", {"fingerprint": fingerprint, "response": response, "status": status}, ident)


def text(value, name, *, required=False, max_len=4000):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise APIError(f"{name} must be text.")
    value = value.strip()
    if required and not value:
        raise APIError(f"{name} is required.")
    if len(value) > max_len:
        raise APIError(f"{name} is too long.")
    return value


def number(value, name, *, minimum=0, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise APIError(f"{name} must be a finite number.")
    if value < minimum or (positive and value <= minimum):
        raise APIError(f"{name} must be {'greater than' if positive else 'at least'} {minimum}.")
    if value > 1e12:
        raise APIError(f"{name} is too large.")
    return value


def crew_ref(value, *, required=True):
    value = text(value, "crew_id", required=required, max_len=128)
    if value and not get_record("crew", value):
        raise APIError("Crew member not found.", 404)
    return value


def require_record(kind, record_id):
    record = get_record(kind, record_id)
    if not record:
        raise APIError("Record not found.", 404)
    return record


def timestamp(value=None):
    value = value or now()
    if not isinstance(value, str):
        raise APIError("Invalid UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise APIError("Invalid UTC timestamp.")
    if parsed.tzinfo is None:
        # Core now() is defined as UTC; tolerate a naive UTC string from core.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def log(action, kind, ident, before=None, after=None, *, category="operations", detail=None):
    record_audit(action, kind, ident, before=before, after=after)
    record_event(category, ident, detail or action.replace("_", " "))


def make_history(kind, record_id, action, snapshot):
    revisions = [r for r in list_records(kind + "_revision") if r.get("record_id") == record_id]
    return insert_record(kind + "_revision", {
        "record_id": record_id, "action": action, "actor_id": actor(),
        "actor": actor_name(), "timestamp": now(), "revision": len(revisions) + 1, "snapshot": snapshot,
    })


def with_history(kind, record):
    result = dict(record)
    revisions = [r for r in list_records(kind + "_revision") if r.get("record_id") == record["id"]]
    revisions.sort(key=lambda r: (r.get("revision", 0), r.get("timestamp", ""), r.get("id", "")))
    result["history"] = revisions
    result["revisions"] = revisions
    return result


def medical_values(data, partial=False):
    out = {}
    if not partial or "crew_id" in data:
        out["crew_id"] = crew_ref(data.get("crew_id"))
    for key in ("category", "note", "follow_up"):
        if not partial or key in data:
            out[key] = text(data.get(key), key, required=(key in ("category", "note")), max_len=12000 if key != "category" else 100)
    return out


@ops.route("/api/medical-records", methods=["GET", "POST"])
@require_roles(*MEDICAL)
@safe
def medical_records():
    if request.method == "GET":
        crew_id = request.args.get("crew_id")
        records = list_records("medical_record")
        if crew_id:
            records = [r for r in records if r.get("crew_id") == crew_id]
        return jsonify([with_history("medical_record", r) for r in records])
    data = body()
    begin()
    values = medical_values(data)
    values.update(created_by=actor(), creator=actor_name())
    record = insert_record("medical_record", values)
    make_history("medical_record", record["id"], "created", record)
    log("medical_record_create", "medical_record", record["id"], after={"crew_id": record["crew_id"], "category": record["category"]}, category="medical", detail="A medical record was created.")
    commit()
    return jsonify(with_history("medical_record", record)), 201


@ops.route("/api/medical-records/<record_id>", methods=["PATCH"])
@require_roles(*MEDICAL)
@safe
def medical_update(record_id):
    data = body()
    begin()
    old = require_record("medical_record", record_id)
    patch = medical_values(data, partial=True)
    if not patch:
        raise APIError("No editable medical fields supplied.")
    patch["updated_by"] = actor()
    updated = update_record("medical_record", record_id, patch)
    make_history("medical_record", record_id, "updated", updated)
    # Audit deliberately identifies changed fields rather than clinical text.
    log("medical_record_update", "medical_record", record_id,
        before={"crew_id": old.get("crew_id"), "category": old.get("category")},
        after={"crew_id": updated.get("crew_id"), "category": updated.get("category"), "changed_fields": sorted(k for k in patch if k != "updated_by")},
        category="medical", detail="A medical record was revised.")
    commit()
    return jsonify(with_history("medical_record", updated))


def medication_values(data, partial=False):
    out = {}
    for key, required, limit in (("name", True, 200), ("unit", True, 40), ("storage", False, 1000)):
        if not partial or key in data:
            out[key] = text(data.get(key), key, required=required, max_len=limit)
    for key in ("quantity", "low_stock_threshold"):
        if not partial or key in data:
            out[key] = number(data.get(key, 0), key)
    if not partial or "expiry_date" in data:
        expiry = text(data.get("expiry_date"), "expiry_date", max_len=10)
        if expiry:
            try:
                if date.fromisoformat(expiry).isoformat() != expiry:
                    raise ValueError()
            except ValueError:
                raise APIError("expiry_date must be a date in YYYY-MM-DD format.")
        out["expiry_date"] = expiry
    return out


def medication_view(record):
    item = dict(record)
    item["low_stock"] = item.get("quantity", 0) <= item.get("low_stock_threshold", 0)
    expiry = item.get("expiry_date")
    try:
        days = (date.fromisoformat(expiry) - timestamp().date()).days if expiry else None
    except (ValueError, TypeError):
        days = None
    item["expired"] = days is not None and days < 0
    mission = get_record("settings", "mission") or {}
    lead = mission.get("expiry_warning_days", current_app.config["EXPIRY_WARNING_DEFAULT"])
    item["expiry_warning_days"] = lead
    item["expiring_soon"] = days is not None and 0 <= days <= lead
    return item


@ops.route("/api/medications", methods=["GET", "POST"])
@require_roles(*MEDICAL)
@safe
def medications():
    if request.method == "GET":
        return jsonify([medication_view(x) for x in list_records("medication")])
    data = body()
    begin()
    values = medication_values(data)
    values["created_by"] = actor()
    record = insert_record("medication", values)
    log("medication_create", "medication", record["id"], after={"quantity": record["quantity"], "unit": record["unit"]}, category="medication", detail="Medication inventory item created.")
    commit()
    return jsonify(medication_view(record)), 201


@ops.route("/api/medications/<record_id>", methods=["PATCH"])
@require_roles(*MEDICAL)
@safe
def medication_update(record_id):
    data = body()
    begin()
    old = require_record("medication", record_id)
    patch = medication_values(data, partial=True)
    if not patch:
        raise APIError("No editable medication fields supplied.")
    updated = update_record("medication", record_id, patch)
    audited_fields = ("quantity", "low_stock_threshold", "expiry_date")
    log("medication_update", "medication", record_id,
        before={k: old.get(k) for k in audited_fields if k in patch},
        after={**{k: updated.get(k) for k in audited_fields if k in patch}, "changed_fields": sorted(patch)},
        category="medication", detail="Medication inventory updated.")
    commit()
    return jsonify(medication_view(updated))


@ops.route("/api/medications/<record_id>/administer", methods=["POST"])
@require_roles(*MEDICAL)
@safe
def administer(record_id):
    data = body()
    begin()
    ident, fingerprint, previous = idempotency_lookup(data)
    if previous:
        commit()
        return jsonify(previous["response"]), previous["status"]
    medication = require_record("medication", record_id)
    crew_id = crew_ref(data.get("crew_id"))
    quantity = number(data.get("quantity"), "quantity", positive=True)
    note = text(data.get("note"), "note", max_len=12000)
    available = Decimal(str(medication.get("quantity", 0)))
    requested = Decimal(str(quantity))
    if requested > available:
        raise APIError("Insufficient medication stock.", 409)
    when = now()
    admin = insert_record("administration", {
        "medication_id": record_id, "medication_name": medication.get("name"),
        "crew_id": crew_id, "quantity": quantity, "unit": medication.get("unit"),
        "note": note, "actor_id": actor(), "actor": actor_name(),
        "administered_by": actor(), "administered_at": when, "timestamp": when,
    })
    stock = update_record("medication", record_id, {"quantity": float(available - requested)})
    log("medication_administer", "administration", admin["id"],
        before={"medication_id": record_id, "stock": medication["quantity"]},
        after={"medication_id": record_id, "crew_id": crew_id, "quantity": quantity, "unit": medication.get("unit"), "stock": stock["quantity"]},
        category="medication", detail="A medication administration was recorded.")
    idempotency_store(ident, fingerprint, admin, 201)
    commit()
    return jsonify(admin), 201


@ops.route("/api/administrations", methods=["GET"])
@require_roles(*MEDICAL)
@safe
def administrations():
    records = list_records("administration")
    for key in ("crew_id", "medication_id"):
        if request.args.get(key):
            records = [r for r in records if r.get(key) == request.args[key]]
    return jsonify(records)


DEFAULT_CHECKLIST = [
    {"text": "Confirm and document the situation with the responsible operator.", "done": False},
    {"text": "Record actions and observations for review.", "done": False},
    {"text": "Review the incident with authorized personnel.", "done": False},
]


def checklist_value(value):
    if not isinstance(value, list) or len(value) > 100:
        raise APIError("checklist must be an array of up to 100 items.")
    result = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("done"), bool):
            raise APIError("Each checklist item requires text and a boolean done value.")
        result.append({"text": text(item.get("text"), "checklist text", required=True, max_len=1000), "done": item["done"]})
    return result


def incident_values(data, partial=False):
    out = {}
    if "sensitivity" in data:
        sensitivity = text(data.get("sensitivity"), "sensitivity", required=True, max_len=20).lower()
        if sensitivity not in {"operational", "medical"}:
            raise APIError("sensitivity must be operational or medical.")
        out["sensitivity"] = sensitivity
    elif not partial:
        # Old records without a classification are treated as medical. New
        # nonmedical reports are expressly operational-only.
        out["sensitivity"] = "medical" if (current_user() or {}).get("role") in MEDICAL else "operational"
    if not partial or "title" in data:
        out["title"] = text(data.get("title"), "title", required=True, max_len=200)
    if not partial or "severity" in data:
        severity = text(data.get("severity", "WARNING"), "severity", required=True, max_len=30).upper()
        if severity not in {"INFORMATIONAL", "LOW", "MODERATE", "MEDIUM", "HIGH", "WARNING", "CRITICAL"}:
            raise APIError("Invalid incident severity.")
        out["severity"] = severity
    if not partial or "crew_id" in data:
        out["crew_id"] = crew_ref(data.get("crew_id"), required=False)
    for key, limit in (("assigned_to", 128), ("report", 12000)):
        if not partial or key in data:
            out[key] = text(data.get(key), key, max_len=limit)
    if not partial or "checklist" in data:
        out["checklist"] = checklist_value(data.get("checklist", DEFAULT_CHECKLIST))
    if not partial or "status" in data:
        status = text(data.get("status", "open"), "status", required=True, max_len=30).lower()
        if status not in {"open", "acknowledged", "resolved"}:
            raise APIError("Incident status must be open, acknowledged, or resolved.")
        out["status"] = status
    return out


def incident_transition(old, patch):
    previous = old.get("status", "open").lower()
    next_status = patch.get("status", previous)
    if previous == "resolved" and next_status != "resolved":
        raise APIError("Resolved incidents cannot be reopened; create a new incident.", 409)
    if previous == "acknowledged" and next_status == "open":
        raise APIError("An acknowledged incident cannot return to open.", 409)
    if next_status in ("acknowledged", "resolved") and not old.get("acknowledged_at"):
        patch["acknowledged_at"] = now()
        patch["acknowledged_by"] = actor()
    if next_status == "resolved" and previous != "resolved":
        patch["resolved_at"] = now()
        patch["resolved_by"] = actor()
    return patch


def incident_visible(record):
    return (current_user() or {}).get("role") in MEDICAL or record.get("sensitivity") == "operational"


@ops.route("/api/incidents", methods=["GET", "POST"])
@require_roles(*ALL_ROLES)
@safe
def incidents():
    if request.method == "GET":
        return jsonify([with_history("incident", r) for r in list_records("incident") if incident_visible(r)])
    data = body()
    begin()
    values = incident_values(data)
    if values["sensitivity"] == "medical" and (current_user() or {}).get("role") not in MEDICAL:
        raise APIError("Medical incidents require a Medical Officer or Administrator.", 403)
    values.update(created_by=actor(), creator=actor_name(), checklist_notice=DISCLAIMER)
    incident_transition({}, values)
    record = insert_record("incident", values)
    make_history("incident", record["id"], "created", record)
    log("incident_create", "incident", record["id"], after={"severity": record["severity"], "status": record["status"], "crew_id": record.get("crew_id")}, category="operations", detail="An incident was created.")
    commit()
    return jsonify(with_history("incident", record)), 201


def update_incident(record_id, data):
    begin()
    old = require_record("incident", record_id)
    if not incident_visible(old):
        raise APIError("Record not found.", 404)
    patch = incident_values(data, partial=True)
    if not patch:
        raise APIError("No editable incident fields supplied.")
    if patch.get("sensitivity") == "medical" and (current_user() or {}).get("role") not in MEDICAL:
        raise APIError("Medical incidents require a Medical Officer or Administrator.", 403)
    if patch.get("sensitivity") == "operational" and old.get("sensitivity") != "operational":
        raise APIError("Medical and unclassified incidents cannot be downgraded to operational.", 409)
    incident_transition(old, patch)
    patch["updated_by"] = actor()
    record = update_record("incident", record_id, patch)
    make_history("incident", record_id, "updated", record)
    log("incident_update", "incident", record_id,
        before={"status": old.get("status"), "severity": old.get("severity")},
        after={"status": record.get("status"), "severity": record.get("severity"), "changed_fields": sorted(k for k in patch if k not in {"updated_by"})},
        category="operations", detail="An incident was updated.")
    commit()
    return jsonify(with_history("incident", record))


@ops.route("/api/incidents/<record_id>", methods=["PATCH"])
@require_roles(*ALL_ROLES)
@safe
def incident_patch(record_id):
    return update_incident(record_id, body())


@ops.route("/api/incidents/<record_id>/acknowledge", methods=["POST"])
@require_roles(*ALL_ROLES)
@safe
def incident_ack(record_id):
    return update_incident(record_id, {"status": "acknowledged"})


@ops.route("/api/incidents/<record_id>/resolve", methods=["POST"])
@require_roles(*ALL_ROLES)
@safe
def incident_resolve(record_id):
    data = body(required=False)
    patch = {"status": "resolved"}
    if "report" in data:
        patch["report"] = data["report"]
    return update_incident(record_id, patch)


def settings():
    """Core's settings is a record; the default applies to fresh databases."""
    records = list_records("settings")
    if records:
        return records[0]
    return {"delay_minutes": 18.7, "earth_connected": True, "last_sync": None}


def link_settings():
    config = settings()
    connected = config.get("earth_connected", True)
    if not isinstance(connected, bool):
        connected = bool(connected)
    delay = config.get("delay_minutes", 18.7)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0 or delay > 5256000:
        delay = 18.7
    return config, connected, delay


def message_values(data):
    direction = text(data.get("direction", "outgoing"), "direction", required=True, max_len=20).lower()
    if direction not in {"incoming", "outgoing"}:
        raise APIError("direction must be incoming or outgoing.")
    return {"direction": direction,
            "subject": text(data.get("subject"), "subject", required=True, max_len=200),
            "body": text(data.get("body"), "body", required=True, max_len=12000),
            "priority": text(data.get("priority", "normal"), "priority", required=True, max_len=40).lower()}


def message_history(record, state, at=None):
    history = list(record.get("history") or [])
    history.append({"status": state, "timestamp": at or now(), "actor_id": actor() or getattr(g, "audit_actor", None)})
    return history


def start_transmission(record, delay, at):
    started = iso(at)
    return {"status": "transmitted", "transmitted_at": started,
            "due_at": iso(at + timedelta(minutes=delay)), "delivered_at": None,
            "acknowledged_at": None, "failed_at": None,
            "history": message_history(record, "transmitted", started)}


def create_message(data, connected, delay, record_id=None):
    values = message_values(data)
    values.update(status="queued", transmitted_at=None, due_at=None, delivered_at=None,
                  acknowledged_at=None, failed_at=None, created_by=actor(),
                  attempts=0, history=[{"status": "queued", "timestamp": now(), "actor_id": actor()}])
    # Persist locally first; the same transaction makes the final state durable.
    record = insert_record("message", values, record_id=record_id)
    if connected:
        patch = start_transmission(record, delay, timestamp())
        patch["attempts"] = 1
        record = update_record("message", record["id"], patch)
    log("message_create", "message", record["id"], after={"direction": record["direction"], "priority": record["priority"], "status": record["status"]}, category="communication", detail="A simulated Earth message was queued.")
    return record


def pending_messages():
    rows = get_db().execute("""SELECT id,data,created_at,updated_at FROM records
        WHERE kind='message' AND json_extract(data,'$.status') IN ('queued','transmitted')
        ORDER BY created_at,id""").fetchall()
    result = []
    for row in rows:
        item = json.loads(row["data"])
        item.update(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"])
        result.append(item)
    return result


def message_work_due():
    """Read-only preflight; use exact UTC comparisons, not rounded SQL dates."""
    at = timestamp()
    rows = get_db().execute("""SELECT json_extract(data,'$.status') AS status,
        json_extract(data,'$.due_at') AS due_at FROM records
        WHERE kind='message' AND json_extract(data,'$.status') IN ('queued','transmitted')""")
    for record in rows:
        if record["status"] == "queued":
            return True
        if record["due_at"]:
            try:
                if at >= timestamp(record["due_at"]):
                    return True
            except APIError:
                continue
    return False


def process_messages(connected, delay):
    """Synchronize queued and due transmissions; never deliver early."""
    changed = []
    if not connected:
        return changed
    at = timestamp()
    for record in pending_messages():
        state = record.get("status")
        patch = None
        if state == "queued":
            patch = start_transmission(record, delay, at)
            patch["attempts"] = int(record.get("attempts", 0)) + 1
        elif state == "transmitted" and record.get("due_at"):
            try:
                due = timestamp(record["due_at"])
            except APIError:
                # Invalid historical timestamps cannot become deliveries.
                continue
            if at >= due:
                delivered = iso(at)
                patch = {"status": "delivered", "delivered_at": delivered,
                         "history": message_history(record, "delivered", delivered)}
        if patch:
            updated = update_record("message", record["id"], patch)
            log("message_" + patch["status"], "message", record["id"], before={"status": state}, after={"status": patch["status"], "due_at": updated.get("due_at")}, category="communication", detail="Simulated message status changed to " + patch["status"] + ".")
            changed.append(record["id"])
    return changed


@ops.before_app_request
def remember_connection_before_settings_change():
    if request.path == "/api/settings" and request.method == "PATCH" and current_user():
        g._ops_was_connected = link_settings()[1]


@ops.after_app_request
def synchronize_on_reconnection(response):
    if (request.path != "/api/settings" or request.method != "PATCH" or
            not (200 <= response.status_code < 300) or
            getattr(g, "_ops_was_connected", None) is not False):
        return response
    try:
        config, connected, delay = link_settings()
        if connected:
            begin()
            changed = process_messages(True, delay)
            synced_at = now()
            if config.get("id"):
                update_record("settings", config["id"], {"last_sync": synced_at})
            log("sync_reconnection", "settings", config.get("id") or "mission",
                after={"updated": len(changed), "conflicts": 0, "last_sync": synced_at},
                category="communication", detail="Simulated Earth link reconnected and synchronized.")
            commit()
            response_data = response.get_json(silent=True)
            if isinstance(response_data, dict):
                response_data.update(last_sync=synced_at, sync_required=False)
                response.set_data(json.dumps(response_data, ensure_ascii=False))
    except Exception:
        get_db().rollback()
        # Depending on the host's transaction policy the settings update may
        # already have committed. Either way, make failure explicit.
        return jsonify(error="Reconnection synchronization failed; retry the settings change or synchronization."), 500
    return response


def same_message(existing, values):
    return all(existing.get(k) == v for k, v in values.items())


def message_view(record):
    result = dict(record)
    if result.get("direction") == "incoming":
        try:
            visible = (result.get("status") in {"delivered", "acknowledged"}
                       and bool(result.get("due_at")) and bool(result.get("delivered_at"))
                       and timestamp(result["due_at"]) <= timestamp(result["delivered_at"]) <= timestamp())
        except APIError:
            visible = False
        if not visible:
            result.pop("subject", None)
            result.pop("body", None)
            result["content_pending"] = True
    return result


@ops.route("/api/messages", methods=["GET", "POST"])
@require_roles(*ALL_ROLES)
@safe
def messages():
    data = body() if request.method == "POST" else None
    begin()
    ident, fingerprint, previous = idempotency_lookup(data) if data is not None else (None, None, None)
    if previous:
        commit()
        return jsonify(previous["response"]), previous["status"]
    _, connected, delay = link_settings()
    process_messages(connected, delay)
    if request.method == "GET":
        records = list_records("message")
        commit()
        return jsonify([message_view(r) for r in records])
    record_id = data.get("id") or data.get("client_id")
    if record_id is not None:
        record_id = text(record_id, "id", required=True, max_len=128)
        old = get_record("message", record_id)
        if old:
            if not same_message(old, message_values(data)):
                raise APIError("Message ID conflicts with a different existing message.", 409)
            result = message_view(old)
            idempotency_store(ident, fingerprint, result, 200)
            commit()
            return jsonify(result), 200
    record = create_message(data, connected, delay, record_id)
    result = message_view(record)
    idempotency_store(ident, fingerprint, result, 201)
    commit()
    return jsonify(result), 201


def message_action(record_id, action):
    begin()
    _, connected, delay = link_settings()
    process_messages(connected, delay)
    old = require_record("message", record_id)
    state = old.get("status")
    at = timestamp()
    at_str = iso(at)
    if action == "retry":
        if state != "failed":
            raise APIError("Only failed messages can be retried.", 409)
        if connected:
            patch = start_transmission(old, delay, at)
            patch["attempts"] = int(old.get("attempts", 0)) + 1
        else:
            patch = {"status": "queued", "transmitted_at": None, "due_at": None, "delivered_at": None,
                     "acknowledged_at": None, "failed_at": None, "history": message_history(old, "queued", at_str)}
    elif action == "acknowledge":
        if state == "acknowledged":
            commit()
            return jsonify(message_view(old))
        if state != "delivered" or not old.get("due_at") or timestamp(old["due_at"]) > at:
            raise APIError("Only delivered messages can be acknowledged after their due time.", 409)
        patch = {"status": "acknowledged", "acknowledged_at": at_str, "acknowledged_by": actor(), "acknowledgment_scope": "local_record", "history": message_history(old, "acknowledged", at_str)}
    else:
        if state not in {"queued", "transmitted"}:
            raise APIError("Only queued or transmitted messages can fail.", 409)
        data = body(required=False)
        reason = text(data.get("reason"), "reason", max_len=1000)
        history = message_history(old, "failed", at_str)
        history[-1]["reason"] = reason
        patch = {"status": "failed", "failed_at": at_str, "failure_reason": reason, "history": history}
    updated = update_record("message", record_id, patch)
    log("message_" + action, "message", record_id, before={"status": state}, after={"status": updated["status"]}, category="communication", detail="Simulated message " + action + ".")
    commit()
    return jsonify(message_view(updated))


@ops.route("/api/messages/<record_id>/retry", methods=["POST"])
@require_roles(*ALL_ROLES)
@safe
def message_retry(record_id):
    return message_action(record_id, "retry")


@ops.route("/api/messages/<record_id>/acknowledge", methods=["POST"])
@require_roles(*ALL_ROLES)
@safe
def message_ack(record_id):
    return message_action(record_id, "acknowledge")


@ops.route("/api/messages/<record_id>/fail", methods=["POST"])
@require_roles(*ALL_ROLES)
@safe
def message_fail(record_id):
    return message_action(record_id, "fail")


@ops.route("/api/sync", methods=["POST"])
@require_roles(*ALL_ROLES)
@safe
def synchronize():
    data = body(required=False)
    incoming = data.get("messages", data.get("records", []))
    if not isinstance(incoming, list) or len(incoming) > 1000:
        raise APIError("messages must be an array of at most 1000 records.")
    begin()
    config, connected, delay = link_settings()
    if not connected:
        raise APIError("Earth link is disconnected; messages remain queued locally.", 409)
    duplicates, conflicts, added = [], [], []
    for item in incoming:
        if not isinstance(item, dict):
            raise APIError("Each synchronization record must be an object.")
        ident = text(item.get("id"), "id", required=True, max_len=128)
        values = message_values(item)
        old = get_record("message", ident)
        if old:
            if same_message(old, values):
                duplicates.append(ident)
            else:
                conflicts.append({"id": ident, "reason": "Same ID has different message content; existing local record was preserved."})
        else:
            create_message(item, connected, delay, ident)
            added.append(ident)
    changed = process_messages(connected, delay)
    synced_at = now()
    if config.get("id"):
        update_record("settings", config["id"], {"last_sync": synced_at})
    log("sync", "settings", config.get("id") or "mission", after={"added": len(added), "duplicates": len(duplicates), "conflicts": len(conflicts), "last_sync": synced_at}, category="communication", detail="Simulated Earth synchronization completed.")
    commit()
    return jsonify({"last_sync": synced_at, "added": added, "duplicates": duplicates,
                    "conflicts": conflicts, "updated": changed, "connected": True})


def safe_crew(record, privileged):
    result = dict(record)
    if not privileged:
        for key in ("medical_history", "baseline", "current_health", "diagnosis", "notes"):
            result.pop(key, None)
    return result


def export_payload():
    user = current_user() or {}
    privileged = user.get("role") in MEDICAL
    payload = {"notice": DISCLAIMER, "generated_at": now()}
    sources = (("crew", "crew"), ("measurements", "measurement"), ("radiation", "radiation"),
               ("environment", "environment"), ("alerts", "alert"), ("events", "event"),
               ("incidents", "incident"), ("messages", "message"))
    for name, kind in sources:
        records = list_records(kind)
        if kind == "crew":
            payload[name] = [safe_crew(r, privileged) for r in records]
        elif kind == "incident":
            payload[name] = [r for r in records if incident_visible(r)]
        elif kind == "message":
            payload[name] = [message_view(r) for r in records]
        else:
            payload[name] = records
    payload["settings"] = settings()
    if privileged:
        payload["medical_records"] = [with_history("medical_record", r) for r in list_records("medical_record")]
        payload["medications"] = [medication_view(r) for r in list_records("medication")]
        payload["administrations"] = list_records("administration")
    return payload


def csv_cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    elif value is None:
        value = ""
    else:
        value = str(value)
    # Neutralize spreadsheet formulas, including leading whitespace/control chars.
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        value = "'" + value
    return value


def export_csv(payload):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["section", "record_id", "field", "value"])
    for section, value in payload.items():
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict):
                ident = entry.get("id", "")
                for key, val in entry.items():
                    writer.writerow([csv_cell(section), csv_cell(ident), csv_cell(key), csv_cell(val)])
            else:
                writer.writerow([csv_cell(section), "", "", csv_cell(entry)])
    return out.getvalue().encode("utf-8-sig")


def export_pdf(payload):
    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=letter)
    pdf.setTitle("CHECKPOINT simulation export")
    width, height = letter
    y = height - 42
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(38, y, "CHECKPOINT | SIMULATION EXPORT")
    y -= 22
    pdf.setFont("Helvetica", 8)
    # Canvas text avoids markup interpretation of untrusted record contents.
    for line in export_csv(payload).decode("utf-8-sig").splitlines():
        printable = "".join(ch if ch.isprintable() else " " for ch in line)
        for part in textwrap.wrap(printable, width=110, replace_whitespace=False, drop_whitespace=False) or [""]:
            if y < 40:
                pdf.showPage()
                pdf.setFont("Helvetica", 8)
                y = height - 40
            pdf.drawString(38, y, part)
            y -= 11
    pdf.save()
    return out.getvalue()


@ops.route("/api/export/<format>", methods=["GET"])
@require_roles(*ALL_ROLES)
@safe
def export_data(format):
    if format not in {"json", "csv", "pdf"}:
        raise APIError("Export format must be json, csv, or pdf.", 404)
    begin()
    payload = export_payload()
    if format == "json":
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        mimetype = "application/json"
    elif format == "csv":
        data = export_csv(payload)
        mimetype = "text/csv; charset=utf-8"
    else:
        data = export_pdf(payload)
        mimetype = "application/pdf"
    log("export", "export", format, after={"format": format, "role": (current_user() or {}).get("role")}, category="operations", detail="A role-filtered simulation report was exported.")
    commit()
    return Response(data, content_type=mimetype, headers={"Content-Disposition": f'attachment; filename="checkpoint-export.{format}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")


def user_view(row):
    if row is None:
        return None
    row = dict(row)
    return {k: row[k] for k in ("id", "username", "display_name", "role", "active") if k in row}


def read_user(user_id):
    row = get_db().execute("SELECT id, username, display_name, role, active FROM users WHERE id = ?", (user_id,)).fetchone()
    return user_view(row)


def validate_username(value):
    username = text(value, "username", required=True, max_len=64)
    if not USERNAME.fullmatch(username):
        raise APIError("username must be 3–64 letters, digits, dots, underscores or hyphens, starting with a letter or digit.")
    return username


def validate_password(value):
    error = password_policy_error(value)
    if error:
        raise APIError(error)
    return value


def validate_role(value):
    if not isinstance(value, str) or value not in ROLES:
        raise APIError("Invalid role.")
    return value


def validate_active(value):
    if not isinstance(value, bool):
        raise APIError("active must be a boolean.")
    return value


@ops.route("/api/users", methods=["GET", "POST"])
@require_roles("Administrator")
@safe
def users():
    if request.method == "GET":
        rows = get_db().execute("SELECT id, username, display_name, role, active FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return jsonify([user_view(row) for row in rows])
    data = body()
    username = validate_username(data.get("username"))
    display_name = text(data.get("display_name", username), "display_name", required=True, max_len=120)
    role = validate_role(data.get("role"))
    password = validate_password(data.get("password"))
    active = validate_active(data.get("active", True))
    hashed = generate_password_hash(password)
    db = begin()
    if db.execute("SELECT 1 FROM users WHERE lower(username) = lower(?)", (username,)).fetchone():
        raise APIError("That username is already in use.", 409)
    ident = str(uid())
    db.execute("INSERT INTO users (id, username, display_name, role, password_hash, active) VALUES (?, ?, ?, ?, ?, ?)", (ident, username, display_name, role, hashed, int(active)))
    result = read_user(ident)
    log("user_create", "user", ident, after=result, category="security", detail="A user account was created.")
    commit()
    return jsonify(result), 201


@ops.route("/api/users/<user_id>", methods=["PATCH"])
@require_roles("Administrator")
@safe
def user_update(user_id):
    data = body()
    patch = {}
    if "username" in data:
        patch["username"] = validate_username(data["username"])
    if "display_name" in data:
        patch["display_name"] = text(data["display_name"], "display_name", required=True, max_len=120)
    if "role" in data:
        patch["role"] = validate_role(data["role"])
    if "active" in data:
        patch["active"] = int(validate_active(data["active"]))
    if "password" in data:
        patch["password_hash"] = generate_password_hash(validate_password(data["password"]))
    if not patch:
        raise APIError("No editable user fields supplied.")
    db = begin()
    old = read_user(user_id)
    if not old:
        raise APIError("User not found.", 404)
    if "password" in data:
        old_hash = db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()["password_hash"]
        if check_password_hash(old_hash, data["password"]):
            raise APIError("New password must differ from the current password.")
    if "username" in patch and db.execute("SELECT 1 FROM users WHERE lower(username) = lower(?) AND id <> ?", (patch["username"], user_id)).fetchone():
        raise APIError("That username is already in use.", 409)
    if old["role"] == "Administrator" and old["active"] and (patch.get("role", old["role"]) != "Administrator" or not patch.get("active", old["active"])):
        count = db.execute("SELECT COUNT(*) FROM users WHERE role = ? AND active = 1", ("Administrator",)).fetchone()[0]
        if count <= 1:
            raise APIError("At least one active administrator must remain.", 409)
    assignments = ", ".join(key + " = ?" for key in patch)
    db.execute("UPDATE users SET " + assignments + " WHERE id = ?", (*patch.values(), user_id))
    if ("password_hash" in patch or patch.get("role", old["role"]) != old["role"] or
            ("active" in patch and not patch["active"])):
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    if "password_hash" in patch:
        # Clear both names if a reset also renames the account; both buckets,
        # the new credential, session revocation and audit commit atomically.
        throttle_clear(old["username"])
        if "username" in patch and patch["username"].lower() != old["username"].lower():
            throttle_clear(patch["username"])
    result = read_user(user_id)
    audit_result = dict(result)
    if "password_hash" in patch:
        audit_result["password_changed"] = True
    log("user_update", "user", user_id, before=old, after=audit_result, category="security", detail="A user account was updated.")
    commit()
    return jsonify(result)
