# CHECKPOINT prototype status

A checked box means the stated **prototype capability exists in the assembled code**, not that the whole corresponding SRS requirement is certified, complete, or independently verified. Unchecked items include gaps, further validation, and intentionally out-of-scope production capabilities. All data and procedures remain demonstration-only.

## Implemented prototype capabilities

- [x] Crew profiles: create, view, and update multiple simulated crew members, with role restrictions and audit records; medical profile fields are restricted to medical roles.
- [x] Physiological measurements: persist supported simulated readings with IDs, timestamps, units, and sources; validate inputs, expose latest values and freshness, and evaluate configurable local thresholds.
- [x] Radiation and environment: store simulated dose increments and environmental readings, expose calculated totals and history, and evaluate configured warning/critical rules. Totals are sums of recorded increments, not independently validated physical exposure estimates.
- [x] Alerts: generate local warning/critical threshold alerts and informational missing/stale alerts, with trigger details, history, and authorized acknowledgement/resolution. Guidance is generic and unverified.
- [x] Trends and logs: display charts from stored measurements with metric, crew, and time filters, plus a categorized mission-event timeline.
- [x] Medical and medication records: restricted operator-entered notes and revision history; inventory, low-stock and configurable expiry indicators; separate administration records with stock updates. No automatic diagnosis is supplied.
- [x] Incident workflow: create and classify incidents, associate crew and an operator, acknowledge/resolve, and record generic editable demonstration checklists and reports. These are not approved procedures.
- [x] Earth Link simulation: local incoming/outgoing message records with priority, configurable delay, queue/transmission/delivery/acknowledgement/failure/retry states, and simulated synchronization with duplicate/conflict reporting. Nothing is sent to Earth.
- [x] Local access and audit mechanisms: four demo roles, server-side authorization, hashed passwords and expiring sessions, plus an append-only-guarded hash-linked audit log with local verification and redacted fields.
- [x] Settings and exports: configure mission settings and thresholds, administer demo users, and produce role-filtered CSV, JSON, and PDF exports with export audit events.
- [x] Application shell: Dashboard, Body, Trends, Earth Link, Log, Medical, Radiation, Environment, Alerts, Emergency, and Settings views, with simulation warnings and responsive styling.
- [x] Automated backend tests and local setup/build/test instructions exist. See `tests/README.md` for the test contract and limitations.

## Unresolved / requires verification

- [ ] Implement and verify a retention/archival process if required. `retention_days` is stored but currently triggers **no deletion or archival**; critical records must not be removed through ordinary actions.
- [ ] Independently verify configurable medication expiry-warning behavior against deployment-specific requirements.
- [ ] Establish and validate radiation accounting semantics, including mission-tagged versus individual increments, to avoid interpreting simple recorded sums as verified exposure estimates.
- [ ] Obtain qualified review and approved clinical/emergency protocols before any contemplated clinical use. Current thresholds, source extracts, guidance, and checklists are unapproved demonstrations; there is no clinical certification.
- [ ] Design and independently review production security, including encryption at rest, secure transport/deployment, credential management, access/privacy controls, and physical security. The public demo credentials and unencrypted SQLite store are not suitable for real personal data.
- [ ] If stronger audit assurance is needed, add an independently protected external anchor or equivalent. Local hash links and SQL guards do not prevent a database owner from deleting or rewriting the chain and recomputing hashes.
- [ ] Implement real external communication and distributed synchronization only if explicitly required; the present link and conflict handling are local simulations, not a real Earth service.
- [ ] Expand and independently verify offline/restart/failure recovery, backup/restore, permissions, security, audit, concurrency, and long-duration behavior on the intended deployment environment. Hosted preview storage is ephemeral.
- [ ] Conduct repeatable browser, load, concurrency, memory, network, and sustained-telemetry performance tests against agreed targets. The existing five-request in-process timing check alone does not establish the SRS targets.
- [ ] Test accessibility, keyboard operation, responsive layouts, and cross-browser behavior with users and suitable tools; styling and basic labels do not by themselves establish accessibility compliance.
- [ ] Complete a requirement-by-requirement SRS acceptance review and independent safety/security assessment before claiming functional completeness, certification, or compliance.

## Current upgrade outcomes

- [x] Strengthen sign-in: persistent failed-login throttling, secure password-change validation, session revocation, security headers and non-cacheable API responses; preserve offline/local operation, existing accounts and role enforcement, and do not claim production-grade protection.
- [x] Add a professional, vivid and genuinely interactive Three.js mission visualization with responsive sizing, keyboard-accessible controls or alternatives, reduced-motion support, off-screen/hidden-tab pause, and a clear fallback where WebGL is unavailable; keep real-looking illustrations identified as simulated.
- [x] Add polished color, hover and focus treatments and transitions across the application, without obscuring alert semantics or usability; preserve mobile layout and respect reduced motion.
- [x] Add a local Operations Desk with simulated, explicitly non-medical tasks and handover notes, searchable/filterable with priorities, statuses, responsibility, timestamps, persistence, role checks and audit trails.
- [x] Add a searchable, keyboard-accessible command palette and navigation shortcuts for actual application modules; preserve existing module behavior and forms.
- [x] Add a non-medical mission briefing generated from the available snapshot, clearly indicate data limitations, and allow safe copy or download without requiring a network connection.
- [x] Verify security and features using isolated databases, type checks and tests, restart and validate Preview, and clearly retain simulation and deployment limitations. Independent security, accessibility and clinical certification remain uncompleted above.
