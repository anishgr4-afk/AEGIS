# CHECKPOINT implementation plan

## Product and boundaries

Build the user's complete CHECKPOINT SRS prototype as a locally executable, offline-first Mars transit simulation. Its data, thresholds, protocols, users, and communication events are demonstration material. The interface must say it is not a medical device or approved clinical guidance. All records and decisions need human review. Keep the supplied body figure and the existing console's dark, space-mission-control visual direction. Preserve original materials in `reference/` for comparison.

## Architecture

- A React/TypeScript presentation layer uses the same-origin Flask API. A single responsive shell exposes Dashboard, Body, Trends, Earth Link, Log, Medical, Radiation, Environment, Alerts, Emergency, and Settings.
- A modular Python Flask application validates all input, authorizes requests server-side, evaluates deterministic configurable rules, records append-only tamper-evident audit events, and stores records in SQLite on local disk. SQLite transactions commit before success; record IDs are unique and measurement records retain units, timestamps, and sources. No online dependency is required for a local installation or for simulated Earth disconnection. This follows the SRS's explicit local-password and offline constraints instead of online-only identity.
- Four prototype roles are enforced server-side; demo credentials and local prototype limitations are conspicuous. Passwords are hashed and sessions expire. Real personnel/health information must not be entered. Encryption at rest, physical-system security, independent medical verification and production hardening are not presumed.
- Earth Link is a simulation: outgoing messages are persisted before transmission; due times use configurable one-way delay (default 18.7 minutes; round-trip 37.4), with offline queuing, explicit failure/retry/acknowledgement and synchronization bookkeeping. Do not mark delivery early. Conflict and duplicate behavior must be explicit.
- Seed reproducible, clearly labelled simulated readings for several crew members, recent health and environment histories, medications and events. Trends chart stored records rather than hard-coded chart numbers. Incorporate provided source documents as unverified source material, not approved medical protocols.
- Flask serves the built React frontend and its API on one configured port. The managed Preview is for convenience and is not a stand-in for deployment on a durable local computer. Container disk is ephemeral; document this and do not promise durable production storage or entirely offline browser access to the hosted preview.

## Project structure

- `backend/` — database/store helpers, auth, telemetry, thresholds, alerts, simulation, operational blueprints, and exports.
- `src/` — React shell, reusable UI, module views, styling and API client.
- `public/` — supplied body image and route manifest.
- `reference/` — original project, specification and source PDFs.
- `tests/` — automated unit, integration, offline, permission, integrity and timing checks.
- `data/` — runtime SQLite state (not committed); configure via environment variables for reproducible tests.

## Visual and interaction direction

Reuse the supplied mission-control design: near-black/navy canvas, soft blue panels and fine outlines, cyan telemetry, restrained amber/green/red statuses with explicit textual labels. A navigation rail and structured content panes prioritize situational awareness. Use the supplied body figure for the individual crew view. Type hierarchy pairs a clean geometric sans with monospaced labels; include local/system fallbacks to avoid network font dependencies. Keep keyboard focus visible, respect reduced-motion preferences, and adapt for laptop and narrower viewports. Buttons perform real actions and error/empty/loading states are explicit.

## Delivery

Provide a reproducible local startup with pinned dependencies, tests, local data location, demo credentials and a concise requirements/limitations summary. Save the deliverable as a version checkpoint and show the running application in Preview. No publishing or external data transmission is implied.

## Security and professional console upgrade

Preserve the existing offline-first simulated mission console, data, roles, and local credentials. Strengthen the local authentication boundary with persistent failed-login throttling, safer HTTP response headers and no-store policies, an account password-change flow with server-side validation and session revocation, and clear security-status information. Never misrepresent the hosted preview or unencrypted SQLite database as production-grade or suitable for real medical information.

Add a professional, vibrant orbital visual layer: an original, procedurally generated Three.js mission visualization, cyan/indigo/violet and warm Mars accents, layered light, restrained depth, polished motion and hover/focus feedback. Keep text high-contrast and serious; honor reduced-motion settings, pause hidden/off-screen animations, offer a WebGL fallback and avoid any network asset dependency. Keep the supplied body illustration and all existing workflows intact.

Expand the product with a persistent, locally stored Operations Desk for simulated non-medical tasks and handover notes, searchable and filterable, with priorities, ownership, completion states, timestamps, authorization and audit trails. Add a searchable keyboard-accessible command palette and shortcuts for navigating real modules. Use isolated components and CSS modules under `src/views/`, a dedicated Flask blueprint under `backend/`, and existing SQLite record helpers. Test permissions and critical flows against isolated databases; verify preview and build; never claim clinical approval or independent security certification.
