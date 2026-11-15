import type { ClinicalVitals, TriageFlag, TriageUrgency } from '../data';

const FIELDS: { key: keyof ClinicalVitals; label: string; unit: string; step: number; min: number; max: number }[] = [
  { key: 'body_temp_c', label: 'Body temperature', unit: '°C', step: .1, min: 30, max: 43 },
  { key: 'spo2_pct', label: 'SpO₂', unit: '%', step: 1, min: 50, max: 100 },
  { key: 'heart_rate_bpm', label: 'Heart rate', unit: 'bpm', step: 1, min: 25, max: 220 },
  { key: 'bp_systolic', label: 'Blood pressure · systolic', unit: 'mmHg', step: 1, min: 50, max: 250 },
  { key: 'bp_diastolic', label: 'Blood pressure · diastolic', unit: 'mmHg', step: 1, min: 25, max: 150 },
  { key: 'respiratory_rate_bpm', label: 'Respiratory rate', unit: '/min', step: 1, min: 1, max: 80 },
];

export function VitalsColumn({ vitals, triage, onChange, onSave, saving, scenarios, onScenario, simulated }: {
  vitals: ClinicalVitals; triage: { urgency: TriageUrgency; flags: TriageFlag[] };
  onChange: (value: ClinicalVitals) => void; onSave: () => void; saving: boolean;
  scenarios: readonly { id: string; label: string }[]; onScenario: (name: string) => void; simulated: boolean;
}) {
  const set = (key: keyof ClinicalVitals, value: number) => onChange({ ...vitals, [key]: value });
  return <section className="panel vitals-console" id="vitals">
    <div className="section-head"><div><span className="eyebrow">CREW MEDICAL OFFICER</span><h1>Vitals & triage</h1></div><span className={`urgency urgency--${triage.urgency}`}>{triage.urgency.toUpperCase()}</span></div>
    <div className="vital-inputs">{FIELDS.map(field => <label className="input-card" key={field.key}>
      <span>{field.label}</span><div><input type="number" step={field.step} min={field.min} max={field.max} value={vitals[field.key]} onChange={e => set(field.key, Number(e.target.value))} /><small>{field.unit}</small></div>
      {field.key === 'body_temp_c' && <small className="input-hint">{(vitals.body_temp_c * 9 / 5 + 32).toFixed(1)} °F</small>}
    </label>)}</div>
    {triage.flags.length > 0 ? <div className="flag-list">{triage.flags.map(flag => <div className={`triage-flag triage-flag--${flag.severity}`} key={flag.id}><b>{flag.severity.toUpperCase()}</b><span>{flag.label}: {flag.value} {flag.unit}</span><small>{flag.threshold}</small></div>)}</div> : <div className="routine-note">No configured threshold flags for these readings.</div>}
    <button className="primary-button" onClick={onSave} disabled={saving}>{saving ? 'Saving…' : 'Save readings to MET log'}</button>
    <div className="scenario-picker"><div className="eyebrow">DEMO SCENARIOS</div>{!simulated && <small>Enable simulated data to load presets.</small>}<div className="scenario-buttons">{scenarios.map(item => <button key={item.id} disabled={saving || !simulated} onClick={() => onScenario(item.id)}>{item.label}</button>)}</div></div>
    <p className="medical-note">Threshold output is deterministic decision support based on the supplied demo rules. It is not a diagnosis or treatment direction.</p>
  </section>;
}
