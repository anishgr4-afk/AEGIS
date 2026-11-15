# Lifeline page ↔ backend: plan

Status as of 15 Nov 2026. `Lifeline_Emergency.html` is served by Flask at
`/lifeline` and already shows live wearable data. This plan covers what is
still hard-coded in the page and how to move it to the backend.

## Done (phase 1)

| What the page shows | Where it comes from now |
|---|---|
| Crew list and **every crewmember's** live vitals | `GET /api/lifeline/live`, polled every 2 s. Roster from `data/crew.json`; one telemetry store per person. Values are the backend's rolling medians, colours are its triage flags (`telemetry.py`). |
| Earth link delay | `comms` in the same response (distance ÷ speed of light, `comms.py`). |
| Ranked procedure list above the cards | `POST /api/checklist/match` with the crewmember, filter chips and their live vitals. Engine order (urgency tier, then the procedure a rule names first, then airway > breathing > circulation). Top 3 shown, rest behind "Show more". With crew inputs, the cards show only matched procedures in the same order. |
| Extra filter chips ("What else do they report?", "What else do you see?") | `GET /api/checklist/inputs`. New entries in `data/checklist_triage.json` appear automatically. |

Still hard-coded: the 13 emergency procedures and their steps, the filter
tags (now only used to browse when no crew input is selected), the MET clock,
and the Earth link connected / not connected toggle.

## Phase 2: proposed order

1. ~~Crew from the backend.~~ Done: `crew` on telemetry samples,
   `simulator.py --crew`, one store per crewmember.
2. **Push instead of poll.** A server-sent-events stream
   (`GET /api/lifeline/stream`, ~1 Hz) replaces the 2 s poll. No new
   dependencies: Flask can stream SSE as is.
3. **One procedure list.** Move the page's 13 emergencies (`E`) into a backend
   JSON keyed by checklist section id (`cpr`, `shock`, …), served at
   `GET /api/procedures`. Cards, search and the PDF strip would then share
   ids, and a card could show "matched by live vitals".
4. **Chip mapping owned by the engine.** `toBackend()` in the page translates
   chips (e.g. *Pain* + *Eyes*) to engine inputs. Move that table into
   `checklist_triage.json` so all triage logic lives in one place
   ("the engine owns triage").
5. **Mission clock and link state** from `/api/lifeline/live` (mission day,
   MET, environment), plus an Earth-distance control that calls
   `POST /api/environment`.
6. **Audit trail.** `POST /api/lifeline/event` when a procedure or PDF is
   opened or chips change. These go in the MET audit log and the
   flight-surgeon packet, so the surgeon sees what the crew did during the
   comms delay.

## Decisions needed from the team

- **Medication text in the page.** The page's procedure steps include drug
  names and doses (`dose:` fields in `E`). The project rule is that
  app-written text never names drugs or doses; the checklist PDFs show them
  unmodified with `*` highlighted. Remove them, or keep them and change the rule?
- **Merge with the React UI in `src/`?** Or does Lifeline replace it?
