# Aegis: Autonomous Emergency Guidance In Space

NASA Space Apps Challenge 2026 · Team Space_Docs · Challenge: *Create Health Monitoring Software for Astronauts on Space Missions*

An offline-first emergency console for astronauts on deep-space missions, built for a tablet. On a Mars transit the flight surgeon is 12.5 minutes away each way, so the crew acts first. A crewmate taps what they see, live wearable vitals fill in the rest, and Aegis ranks the matching procedures from NASA's Medical Checklist and opens the original pages.

> **Simulated demo, not a medical device.** Aegis points to NASA's own procedures. It never diagnoses, and app-written text never suggests drugs or doses. Wearable data is simulated.

**Demo:** [add link to slides or video]

## Run it

You need Python 3.10 or newer.

```bash
# 1. Get the code
git clone https://github.com/anishgr4-afk/AEGIS.git
cd AEGIS

# 2. Create a virtual environment and install / Optional
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

# 3. Terminal 1: start the app (Flask API + page on port 5000)
python app.py
```

Open **http://localhost:5000/** in a browser. The page is also served at `/lifeline`.

Start the wearable simulator in a **second terminal**, after the app is running:

```bash
# activate the same virtual environment first, then:
python simulator.py
```

The page then shows live vitals for the four crew members. Without the simulator the page loads but vitals read "not measured".

### Simulator options

```bash
python simulator.py --help
python simulator.py --speed 20                                  # run faster than real time
python simulator.py --crew MS2=acute_febrile,PLT=post_eva_dcs   # pick a scenario per crewmember
```

Anyone not listed is nominal. By default one crewmember (S. Duarte, MS2) is simulated in hypovolemic shock so the demo has something to show.

Scenarios: `nominal_transit`, `acute_febrile`, `post_eva_dcs`, `hypovolemic_shock`.

### Try it

1. With the app and simulator running, open the page.
2. Pick a crew member and watch their vitals and status ("Watch", "Act now").
3. Tap what you see or what the crewmate reports (for example bleeding, chest pain or a burn).
4. Ranked checklist procedures appear. Open one to see the NASA checklist pages, with surgeon-approval medications highlighted.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

## Regenerate the per-procedure PDFs (optional)

The split checklist PDFs in `data/checklist/` are already committed. To rebuild them from `Medical Checklist Rev H.pdf`:

```bash
pip install -r requirements-pdf.txt
python scripts/split_checklist.py
```

## How it works

| Path | What |
|---|---|
| `app.py`, `clinical.py`, `generate_data.py` | Flask backend and shared vital-sign thresholds |
| `telemetry.py` | Wearable ingest and rolling-median triage, one store per crewmember |
| `simulator.py` | Simulated Garmin watch, Astroskin shirt and glucose patch for the whole crew |
| `checklist.py` | Emergency lookup: symptoms, observed signs and vitals → checklist procedures |
| `comms.py` | Earth distance → light-speed delay |
| `data/checklist_triage.json` | The decision rules; each cites a checklist page |
| `data/checklist/` | One PDF per checklist procedure, with surgeon-approval medications highlighted |
| `Lifeline_Emergency.html` | The console page. One self-contained file with fonts and photos embedded, so it needs no network |
| `docs/` | Backend plan and session notes |

Safety rules the engine follows:

- A missing reading is "not measured", never "normal". Uncertainty escalates and never clears.
- Rate and blood-pressure rules pause during exercise and recovery.
- Wrist-worn skin temperature never triggers the fever rule.
- Every rule cites a checklist page. The app shows the checklist's own pages unmodified.

## Acknowledgements

**NASA resources used**

- NASA ISS/Shuttle Medical Checklist (Rev H), the source of every procedure the app opens
- NASA-STD-3001, Volume 1, Rev C (Crew Health)

**Other resources**

- Flask, Flask-CORS, pypdf, pdfplumber, pypdfium2 (Python libraries)
- Fraunces and Manrope typefaces (Google Fonts, SIL Open Font License)

**Use of AI (Super Intelligence)**

Parts of this project's code were written with Claude (Anthropic), an AI assistant, and reviewed and directed by the team. This includes the telemetry processing, checklist lookup, wearable simulator, PDF splitting script and page styling. No NASA logos or branding were generated or modified.
