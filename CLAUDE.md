# CLAUDE.md

Hackathon project: a crew-health console for a single astronaut on a Mars
transit. The core feature is an emergency checklist lookup. Crewmates report
what they see, live wearable vitals fill in the rest, and the app brings up the
relevant NASA Medical Checklist procedures. The flight surgeon is many
minutes away at light speed, so the crew acts on onboard procedures first.

**Simulated demo, not a medical device.** The app points to NASA's own
procedures. It never diagnoses, and app-authored text never suggests drugs or
doses.

## Running (Windows)

Use a separate terminal for each step. Start Flask before the simulator.

```powershell
.\.venv\Scripts\python.exe app.py          # Flask API on :5000
.\.venv\Scripts\python.exe simulator.py    # wearable feed (optional; --help)
npm run dev                                # placeholder UI on :5173, proxies /api
# Lifeline emergency page: http://localhost:5000/lifeline (served by Flask)
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe scripts\split_checklist.py   # regenerate data/checklist/
```

- **Setup**: `pip install -r requirements.txt -r requirements-pdf.txt`.
- **Node** was installed during a session. A terminal opened before that
  install won't find `npm` - open a new one.
- **Flask runs with `debug=False`.** Restart it after code changes. A restart
  also empties the in-memory wearable buffer.

## Layout

| Path | What |
|---|---|
| `app.py`, `clinical.py`, `generate_data.py` | Teammates' Flask backend and manual-entry thresholds |
| `telemetry.py` | Wearable ingest and rolling-median triage, one store per crewmember (blueprint, `/api/telemetry*`, `?crew=`) |
| `simulator.py` | Garmin, Astroskin and glucose-patch simulator for the whole crew (`--crew MS2=hypovolemic_shock,...`) |
| `data/crew.json` | Crew roster and the default wearer (readings with no crew id go to them) |
| `comms.py` | Earth distance → light-speed comms delay, default distances per environment |
| `checklist.py` | Emergency lookup: symptoms + observed signs + vitals → procedures (`/api/checklist/*`) |
| `data/checklist_triage.json` | The decision tree: rules, observed signs, "not covered" notes. **Draft, needs team review** |
| `data/checklist_sections.json` | Hand-built index of 68 checklist procedures, by printed page range |
| `data/checklist/` | Generated per-procedure PDFs and `manifest.json` (from `scripts/split_checklist.py`) |
| `Lifeline_Emergency.html` | Teammate's emergency page, wired to the backend (live vitals, checklist PDFs). Plan: `docs/lifeline-backend-plan.md` |
| `src/` | Placeholder React UI, owned by another teammate - don't modify without asking |
| `docs/session-notes/` | Per-session history |

## Rules to keep

- **One set of thresholds.**
  - Vitals: `clinical.evaluate_vitals`, shared by manual entry and wearables.
  - Blood sugar: `generate_data.py`.
  - If you change `evaluate_vitals`, the test
    `tests/fixtures/evaluate_vitals_baseline.json` must still pass, so
    manual-entry results stay identical.
- **The engine owns triage.**
  - A missing reading is "not measured", never "normal".
  - Uncertainty escalates; it never clears.
  - Rate and blood-pressure rules pause during exercise and recovery.
- **Wearables measure skin temperature, not core.** Never apply the 38 °C
  fever rule to them. Fever counts only from a thermometer reading.
- **Every checklist rule cites a checklist page** in `source`. Cut-offs that
  are the team's own choice must say so.
- **Medication display.** Show the checklist's own pages unmodified, with
  `*` medications highlighted (Contact Surgeon first). Don't write drug
  names or doses into app-generated text.
- **High-rate data goes to `/api/telemetry`.** `/api/clinical/vitals` writes
  the audit log and a file on every call.

## Gotchas

- **Text extraction from `Medical Checklist Rev H.pdf` is garbled.**
  - Capital "I" becomes `(cid:1)` and dashes become U+FFFD. Normalise with
    `GLYPH_FIXES` in `scripts/split_checklist.py`.
  - The rendered pages are fine.
  - PDF pages 1–16 are front matter and tables of contents; skip them when
    mapping printed page labels.
- **Two meanings of `*` in the checklist.** `*DrugName` means surgeon
  approval. A bare `*` is a boxed-note border, which sits off the text
  baseline, so match it by nearby height.
- **`data/medical_database.json` isn't usable for lookups.** It's a
  teammate's auto-extracted file with fragment entries, empty steps and no
  page numbers. Don't build on it.
- **`data/clinical_state.json` contains bad blood pressure (60/78).** Adding
  a systolic > diastolic check to `validate_vitals` would crash startup until
  the file is fixed.
- **Comms delay is computed, not stored.** `comms.py` derives it from Earth
  distance / speed of light (Mars default 225 million km, 12.5 min one way).
  `POST /api/environment` accepts an optional `earth_distance_km`. Don't
  hardcode `comms_delay_seconds` anywhere.
- **Windows shell quirks.**
  - Printing non-ASCII characters (e.g. `SpO₂`) from Python in PowerShell
    fails with cp1252; write files with `encoding="utf-8"`.
  - Large Bash heredocs containing quotes can fail to parse; prefer the
    Write tool.
