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
# Aegis emergency page: http://localhost:5000/lifeline (served by Flask)
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
| `Lifeline_Emergency.html` | The emergency page: live vitals, ranked procedures, checklist PDFs. Self-contained (fonts and photos embedded). Plan: `docs/lifeline-backend-plan.md` |
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

## The page's look

`Lifeline_Emergency.html` is one self-contained file: no network at run time,
because a console on a spacecraft may not have one. Fonts and crew photos are
embedded as base64.

- **Type**: Fraunces 500 for headings, numbers and vitals; Manrope for body
  text. Only the Fraunces weights in use (400, 500) are embedded, so styling
  something 600+ silently falls back to a system serif.
- **Colour**: ink `#09080D`; mint `#A8C5B3` normal; blush `#D8B7C0` watch;
  `#EF8A96` act now. Severity drives `--c` through `.s0/.s1/.s2`, so a status
  colour is set in one place, not per element.
- **Crew photos** are `.av0`-`.av3`, keyed by crew id in `AV`, so the roster
  still comes from the backend. A crew member with no photo still renders.
- **Say the status in words** ("Watch", "Act now") next to the reading.
  Don't add a warning icon to every out-of-range vital: with six vitals and
  four crew, repeated icons read as noise.
- **Checklist figures need `mix-blend-mode:screen`** over a near-black frame
  (`#050408`). The scans have white backgrounds; without it they are grey
  boxes. A procedure with no figure uses the full width (`.split.solo`)
  rather than showing a placeholder drawing.
- Keep it coherent: one radius (`--r`), one set of panel/line tokens, and
  sentence case in the interface. Earlier versions shouted in all-caps.

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
- **Verifying the page with no browser tool.** Run its script against a stub
  DOM: fake `document.getElementById` returning stub elements, capture the
  `click` handler, point `fetch` at `127.0.0.1:5000`, then assert on the HTML
  the render functions produce. Faster than a real browser and it exercises
  the live backend. The page's script is the last `<script>` block.
- **A teammate's `Lifeline_Emergency_v7.html`** (in `~/Downloads`, not the
  repo) is a fork of this page, not a theme: same 13 guides, nearly the same
  script. Its stylesheet is safe to borrow; its script would revert fixes.
- **Windows shell quirks.**
  - Printing non-ASCII characters (e.g. `SpO₂`) from Python in PowerShell
    fails with cp1252; write files with `encoding="utf-8"`.
  - Large Bash heredocs containing quotes can fail to parse; prefer the
    Write tool.
