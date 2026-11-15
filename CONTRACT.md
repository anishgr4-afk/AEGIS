# Integration contract

All HTTP responses use JSON except export downloads. Successful collection GETs return JSON arrays, single GETs/mutations return JSON objects. Failure returns `{ "error": "clear explanation" }` with appropriate status. Use `/api` same-origin relative paths. Times are UTC ISO 8601 strings. Never return password hashes, bearer-token hashes or sensitive values in normal logs.

## Shared data

All records include `id`, `created_at`, `updated_at` strings. Record shapes:
- `crew`: `name`, `role`, `status`, `baseline`, `medical_history`, `current_health`, `last_telemetry`. `baseline` and `medical_history` can be strings. Restrict sensitive medical fields to medical officer/admin and prevent sensitive records leaking to other roles.
- `measurement`: `crew_id`, `metric`, `value`, `unit`, `source`, `timestamp`. Expected `metric`: `heart_rate`, `bp_systolic`, `bp_diastolic`, `spo2`, `respiratory_rate`, `temperature`, `sleep`, `hydration`, `weight`, `body_fat`; append-only with unique ids.
- `radiation`: `crew_id` (or `mission`), `value` (nonnegative increment), `unit` (`mSv`), `source`, `timestamp`, optional `event`.
- `environment`: `metric` (`cabin_temperature`, `cabin_pressure`, `oxygen`, `co2`, `humidity`, `air_quality`, `radiation`), numeric `value`, `unit`, `source`, `timestamp`.
- `alert`: `entity_type` (`crew`, `environment`, `radiation`), `entity_id`, `metric`, `value`, `unit`, `rule`, `severity` (`INFORMATIONAL`, `WARNING`, `CRITICAL`), `detected_at`, `acknowledged`, `resolved`, and non-prescriptive `guidance`.
- `event`: `category`, `entity`, `detail`, `timestamp`.
- `settings`: `mission_day`, `location`, `delay_minutes`, `earth_connected`, `last_sync`, `retention_days`, `thresholds` (map metric -> `{min,max,critical_min,critical_max,unit}`), optional fields.
- `user`: `id`, `username`, `display_name`, `role` with role values `Commander`, `Medical Officer`, `Mission Operator`, `Administrator`.

## Backend collaboration boundary

`backend/core.py` provides `get_db()`, `now()` returning UTC ISO time, `uid()` returning uuid, `list_records(kind)`, `get_record(kind, id)`, `insert_record(kind, data, record_id=None)`, `update_record(kind, id, patch)`, `record_event(category, entity, detail)` and `record_audit(action, entity, entity_id, before=None, after=None, outcome='success')`. Stored records use a SQLite table with atomic persistence, id and timestamps managed by the helper. Functions run within Flask request/app context. `current_user()` returns the authenticated user dictionary; `require_roles(*roles)` is a decorator that rejects unauthenticated calls with 401 and unauthorized calls with 403. The app calls `init_db()` on startup and registers blueprint `ops` from `backend.ops`. Seed data is installed only on an empty database. `record_event` and `record_audit` do not expose sensitive data in events; do not leak arbitrary medical notes to logs. Route handlers use transactional commits; `record_audit` stores immutable previous/new values, with redaction where appropriate.

## Core API

- `POST /api/auth/login` `{username,password}` -> `{token,user}`; `GET /api/auth/session` -> user; `POST /api/auth/logout`. Use `Authorization: Bearer <token>`; token is stored only in browser sessionStorage, expires, and server enforces roles.
- `GET /api/snapshot` -> `{mission, crew, measurements, radiation, environment, alerts, events, settings}`. The server filters fields by role. `mission` has the same shape as settings plus `last_sync` and computed `radiation_total`.
- `GET/POST /api/crew`; `PATCH /api/crew/<id>`.
- `GET/POST /api/telemetry`, `GET/POST /api/radiation`, `GET/POST /api/environment`.
- `GET /api/alerts`; `POST /api/alerts/<id>/acknowledge`, `POST /api/alerts/<id>/resolve`.
- `GET /api/settings`; `PATCH /api/settings` for configuration and connectivity; `GET /api/events`, `GET /api/audit` (admin only).
- `GET /api/health` is public and returns 200 for service readiness.

## Operations API

- `GET/POST /api/medical-records`, `PATCH /api/medical-records/<id>`; record fields `crew_id, category, note, follow_up`; revision history must persist. Medical officer/admin only.
- `GET/POST /api/medications`, `PATCH /api/medications/<id>`, `POST /api/medications/<id>/administer`; medication fields `name, quantity, unit, expiry_date, storage, low_stock_threshold`; administration `{crew_id, quantity, note}` appends a separate immutable administration with actor/time and reduces stock atomically. Medical officer/admin only.
- `GET /api/administrations`; medical officer/admin only.
- `GET/POST /api/incidents`, `PATCH /api/incidents/<id>`; fields `title, severity, crew_id, assigned_to, status, checklist, report`; checklist is an array of `{text, done}`; history/audit retained.
- `GET/POST /api/messages`, `POST /api/messages/<id>/retry`, `/acknowledge`, `/fail`, `POST /api/sync`; message fields `direction` (`outgoing`/`incoming`), `subject`, `body`, `priority`, `status`, `created_at`, `transmitted_at`, `due_at`, `delivered_at`, `acknowledged_at`. Simulated disconnect queues; never deliver prior to due. Sync tracks last synchronization, deduplicates by record ID and reports conflicts explicitly.
- `GET /api/export/<format>` for `json`, `csv`, `pdf`; enforce authorization, log export, safe human-readable content, true PDF output.
- `GET/POST /api/users`, `PATCH /api/users/<id>` for administrators; validate usernames/roles and passwords, hash passwords. Never return passwords.

## UI component boundary

`src/lib.ts` exports `api(path, options?)`, `User`, `Snapshot`, and `ViewProps = { user: User; data: Snapshot; refresh: () => Promise<void> }`. The api helper supplies bearer token from sessionStorage, parses failures and throws `Error`. `src/views/OperationsViews.tsx` exports `EarthLinkView`, `MedicalView`, `EmergencyView`, `LogView`, `SettingsView`, each accepting `ViewProps`. The core UI imports and renders these and defines Dashboard, Body, Trends, Radiation, Environment and Alerts. Both share styles via existing general-purpose classes plus `src/ops.css`. Operations views fetch protected collections themselves and display actionable errors, waiting and empty states; don't fake data on authorization errors.
