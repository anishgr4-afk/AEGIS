"""Local, simulated and operational-only tasks and shift handovers.

No workspace content is sent to an external service or copied into audit/events.
The blueprint uses the shared record store and the request's database transaction.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException

from .core import current_user, get_db, get_record, insert_record, now, record_audit, record_event, require_roles, update_record

workspace = Blueprint("workspace", __name__)
WRITE_ROLES = ("Commander", "Mission Operator", "Administrator", "Medical Officer")
STATUSES = {"open", "in-progress", "completed"}
PRIORITIES = {"low", "normal", "high", "urgent"}
TASK_FIELDS = {"title", "description", "priority", "status", "assigned_crew_id", "due_date"}
SEARCH_FIELDS = {"query", "status", "priority", "assigned_crew_id", "limit", "offset"}
MAX_BODY = 16 * 1024
MAX_PAGE = 100


class WorkspaceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def safe(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except WorkspaceError as exc:
            get_db().rollback()
            return jsonify(error=exc.message), exc.status
        except HTTPException:
            # Preserve the host application's JSON 413 and other HTTP handlers.
            get_db().rollback()
            raise
        except sqlite3.IntegrityError:
            get_db().rollback()
            return jsonify(error="The record conflicts with existing data."), 409
        except Exception:
            get_db().rollback()
            # Do not log exception text, SQL, or user-supplied content.
            return jsonify(error="The workspace operation could not be completed."), 500
    return wrapper


def body():
    if request.content_length is not None and request.content_length > MAX_BODY:
        raise WorkspaceError("Request body is too large.", 413)
    # Enforce the bound even for requests without a Content-Length header.
    raw = request.get_data(cache=True)
    if len(raw) > MAX_BODY:
        raise WorkspaceError("Request body is too large.", 413)
    try:
        data = request.get_json(silent=True)
    except (ValueError, RecursionError, OverflowError):
        raise WorkspaceError("A valid JSON object is required.")
    if not isinstance(data, dict):
        raise WorkspaceError("A JSON object is required.")
    return data


def only_fields(data, allowed):
    if not data or set(data) - allowed:
        raise WorkspaceError("No fields supplied or unrecognized fields.")


def text(value, name, maximum, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise WorkspaceError(f"{name} must be text.")
    value = value.strip()
    if required and not value:
        raise WorkspaceError(f"{name} is required.")
    if len(value) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise WorkspaceError(f"{name} is invalid or too long (maximum {maximum} characters).")
    return value


def enum(value, name, choices):
    value = text(value, name, 30, required=True)
    if value not in choices:
        raise WorkspaceError(f"Invalid {name}.")
    return value


def crew_id(value):
    value = text(value, "assigned_crew_id", 128)
    if value and get_record("crew", value) is None:
        raise WorkspaceError("Assigned crew member not found.", 404)
    return value or None


def due_date(value):
    value = text(value, "due_date", 10)
    if not value:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise WorkspaceError("due_date must be a valid YYYY-MM-DD date.")
    return value


def task_values(data, partial=False):
    only_fields(data, TASK_FIELDS)
    out = {}
    if not partial or "title" in data:
        out["title"] = text(data.get("title"), "title", 200, required=True)
    if not partial or "description" in data:
        out["description"] = text(data.get("description", ""), "description", 4000)
    if not partial or "priority" in data:
        out["priority"] = enum(data.get("priority", "normal"), "priority", PRIORITIES)
    if not partial or "status" in data:
        out["status"] = enum(data.get("status", "open"), "status", STATUSES)
    if not partial or "assigned_crew_id" in data:
        out["assigned_crew_id"] = crew_id(data.get("assigned_crew_id"))
    if not partial or "due_date" in data:
        out["due_date"] = due_date(data.get("due_date"))
    return out


def begin():
    db = get_db()
    if not db.in_transaction:
        db.execute("BEGIN IMMEDIATE")
    return db


def actor():
    user = current_user()
    if user is None:  # require_roles enforces this before route execution.
        raise WorkspaceError("Authentication required.", 401)
    return user


def metadata(record):
    # Only controlled enums, validated dates and internal crew IDs are logged.
    return {k: record.get(k) for k in ("status", "priority", "assigned_crew_id", "due_date")}


def log(action, kind, ident, before=None, after=None):
    record_audit(action, kind, ident, before=before, after=after)
    record_event("workspace", ident, "A simulated operational workspace record changed.")


def page_value(value, name, default, low, high):
    if value is None:
        return default
    if isinstance(value, bool):
        raise WorkspaceError(f"{name} must be an integer between {low} and {high}.")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 8:
        number = int(value)
    else:
        raise WorkspaceError(f"{name} must be an integer between {low} and {high}.")
    if not low <= number <= high:
        raise WorkspaceError(f"{name} must be an integer between {low} and {high}.")
    return number


def page(filters, kind):
    limit = page_value(filters.get("limit"), "limit", 50, 1, MAX_PAGE)
    offset = page_value(filters.get("offset"), "offset", 0, 0, 1000000)
    db = get_db()
    clauses, params = ["kind=?"], [kind]
    if kind == "workspace_task":
        query = text(filters.get("query", ""), "query", 200)
        if query:
            # SQLite's built-in lower() is ASCII-only; casefold supports Unicode.
            db.create_function("workspace_casefold", 1, lambda value: str(value or "").casefold())
            clauses.append("(instr(workspace_casefold(json_extract(data,'$.title')), ?) > 0 OR instr(workspace_casefold(json_extract(data,'$.description')), ?) > 0)")
            params.extend([query.casefold(), query.casefold()])
        for name, choices in (("status", STATUSES), ("priority", PRIORITIES)):
            value = filters.get(name)
            if value not in (None, ""):
                value = enum(value, name, choices)
                clauses.append(f"json_extract(data,'$.{name}')=?")
                params.append(value)
        assigned = filters.get("assigned_crew_id")
        if assigned not in (None, ""):
            # Filter values are IDs, never an invitation to return crew fields.
            assigned = text(assigned, "assigned_crew_id", 128, required=True)
            clauses.append("json_extract(data,'$.assigned_crew_id')=?")
            params.append(assigned)
    where = " AND ".join(clauses)
    rows = db.execute(f"SELECT id,data,created_at,updated_at FROM records WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", (*params, limit + 1, offset)).fetchall()
    more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        item = json.loads(row["data"])
        item.update(id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"])
        items.append(item)
    response = jsonify(items)
    response.headers.update({"X-Limit": str(limit), "X-Offset": str(offset), "X-Has-More": str(more).lower()})
    return response


@workspace.get("/api/workspace/tasks")
@require_roles()
@safe
def task_list():
    if set(request.args) - {"limit", "offset"} or any(len(request.args.getlist(k)) != 1 for k in request.args):
        raise WorkspaceError("Unsupported or repeated task list parameter.")
    return page(request.args, "workspace_task")


@workspace.post("/api/workspace/tasks/search")
@require_roles()
@safe
def task_search():
    # Search text is in the JSON body, not the access-loggable URL.
    filters = body()
    if set(filters) - SEARCH_FIELDS:
        raise WorkspaceError("Unsupported task search field.")
    return page(filters, "workspace_task")


@workspace.post("/api/workspace/tasks")
@require_roles(*WRITE_ROLES)
@safe
def task_create():
    data = body()
    db = begin()
    values = task_values(data)
    values.update(created_by=actor()["id"], simulated=True)
    record = insert_record("workspace_task", values)
    log("workspace_task_create", "workspace_task", record["id"], after=metadata(record))
    db.commit()
    return jsonify(record), 201


@workspace.get("/api/workspace/tasks/<record_id>")
@require_roles()
@safe
def task_get(record_id):
    record = get_record("workspace_task", record_id)
    if record is None:
        raise WorkspaceError("Task not found.", 404)
    return jsonify(record)


@workspace.patch("/api/workspace/tasks/<record_id>")
@require_roles(*WRITE_ROLES)
@safe
def task_update(record_id):
    data = body()
    db = begin()
    old = get_record("workspace_task", record_id)
    if old is None:
        raise WorkspaceError("Task not found.", 404)
    patch = task_values(data, partial=True)
    changed = sorted(key for key, value in patch.items() if old.get(key) != value)
    if not changed:
        db.commit()
        return jsonify(old)
    patch["updated_by"] = actor()["id"]
    updated = update_record("workspace_task", record_id, patch)
    after = metadata(updated)
    after["changed_fields"] = changed
    log("workspace_task_update", "workspace_task", record_id, before=metadata(old), after=after)
    db.commit()
    return jsonify(updated)


@workspace.delete("/api/workspace/tasks/<record_id>")
@require_roles(*WRITE_ROLES)
@safe
def task_delete(record_id):
    db = begin()
    old = get_record("workspace_task", record_id)
    if old is None:
        raise WorkspaceError("Task not found.", 404)
    db.execute("DELETE FROM records WHERE kind=? AND id=?", ("workspace_task", record_id))
    log("workspace_task_delete", "workspace_task", record_id, before=metadata(old), after={"deleted": True})
    db.commit()
    return jsonify(id=record_id, deleted=True)


@workspace.get("/api/workspace/handovers")
@require_roles()
@safe
def handover_list():
    if set(request.args) - {"limit", "offset"} or any(len(request.args.getlist(k)) != 1 for k in request.args):
        raise WorkspaceError("Unsupported or repeated handover list parameter.")
    return page(request.args, "workspace_handover")


@workspace.post("/api/workspace/handovers")
@require_roles(*WRITE_ROLES)
@safe
def handover_create():
    data = body()
    only_fields(data, {"content"})
    content = text(data.get("content"), "content", 4000, required=True)
    db = begin()
    user = actor()
    record = insert_record("workspace_handover", {"content": content, "author_id": user["id"], "author": user["display_name"], "timestamp": now(), "simulated": True})
    log("workspace_handover_create", "workspace_handover", record["id"], after={"simulated": True})
    db.commit()
    return jsonify(record), 201
