import { useCallback, useEffect, useState } from 'react';
import { api, type MissionSnapshot } from './api';
import { EarthLink } from './components/EarthLink';
import { RightRail } from './components/RightRail';
import { Starfield } from './components/Starfield';
import { TopBar } from './components/TopBar';
import { TriageConsole } from './components/TriageConsole';
import { VitalsColumn } from './components/VitalsColumn';
import { SCENARIOS, type ClinicalState, type ClinicalVitals, type MedicalCatalog } from './data';

const INITIAL: ClinicalVitals = { body_temp_c: 36.7, spo2_pct: 98, heart_rate_bpm: 72, bp_systolic: 118, bp_diastolic: 76, respiratory_rate_bpm: 16 };

export default function App() {
  const [state, setState] = useState<ClinicalState | null>(null);
  const [catalog, setCatalog] = useState<MedicalCatalog | null>(null);
  const [day, setDay] = useState<MissionSnapshot | null>(null);
  const [draft, setDraft] = useState<ClinicalVitals>(INITIAL);
  const [triage, setTriage] = useState<ClinicalState['triage']>({ urgency: 'routine', flags: [] });
  const [region, setRegion] = useState('all');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [simulated, setSimulated] = useState(true);

  const refresh = useCallback(async () => {
    const [clinical, sources, mission] = await Promise.all([api.clinicalState(), api.catalog(), api.day115()]);
    setState(clinical); setDraft(clinical.vitals); setTriage(clinical.triage);
    setCatalog(sources); setDay(mission);
  }, []);

  useEffect(() => { refresh().catch((e: Error) => setError(e.message)); }, [refresh]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      api.evaluateVitals(draft).then(setTriage).catch((e: Error) => setError(e.message));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [draft]);

  const save = async () => {
    setBusy(true); setError('');
    try { const updated = await api.saveVitals(draft, state?.symptoms ?? []); setState(updated); setTriage(updated.triage); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const scenario = async (name: string) => {
    setBusy(true); setError('');
    try { const updated = await api.scenario(name); setState(updated); setDraft(updated.vitals); setTriage(updated.triage); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const toggleSymptom = async (id: string) => {
    if (!state) return;
    const symptoms = state.symptoms.includes(id) ? state.symptoms.filter(x => x !== id) : [...state.symptoms, id];
    setState({ ...state, symptoms });
    try { setState(await api.saveSymptoms(symptoms)); }
    catch (e) { setError((e as Error).message); }
  };
  const audit = async (type: string, detail: string) => {
    try { setState(await api.audit(type, detail)); } catch (e) { setError((e as Error).message); }
  };

  return <div className="app console-app" id="dashboard">
    <Starfield />
    <main className="console-shell">
      <TopBar day={day?.day ?? 115} environment={day?.environment ?? 'Mars Transit'} simulated={simulated} onSimulationChange={setSimulated} />
      <EarthLink commsDelaySeconds={day?.comms_delay_seconds ?? 1120} day={day?.day ?? 115} date={day?.date ?? '2026-11-14'} />
      {error && <div className="inline-error" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss">×</button></div>}
      {!state || !catalog || !day ? <section className="panel loading-card">Loading local crew-health data…</section> : <div className="triage-grid">
        <VitalsColumn vitals={draft} triage={triage} onChange={setDraft} onSave={save} saving={busy} scenarios={SCENARIOS} onScenario={scenario} simulated={simulated} />
        <TriageConsole catalog={catalog} symptoms={state.symptoms} onToggleSymptom={toggleSymptom} region={region} onRegion={setRegion} query={query} onQuery={setQuery} onAudit={audit} />
        <RightRail state={state} triage={triage} catalog={catalog} mission={day} onAudit={audit} />
      </div>}
      <footer className="console-footer">LOCAL OFFLINE CONSOLE · SIMULATED / OPERATOR-ENTERED DATA · SOURCE EXTRACTS REQUIRE CLINICAL REVIEW</footer>
    </main>
  </div>;
}
