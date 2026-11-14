import { useState } from 'react';
import type { MissionSnapshot } from '../api';
import type { ClinicalState, MedicalCatalog } from '../data';

export function RightRail({ state, triage, catalog, mission, onAudit }: { state: ClinicalState; triage: ClinicalState['triage']; catalog: MedicalCatalog; mission: MissionSnapshot; onAudit: (type: string, detail: string) => void }) {
  const [medication, setMedication] = useState('');
  const next = triage.urgency === 'act' ? 'Escalate to the flight surgeon / CMO' : triage.urgency === 'watch' ? 'Recheck readings and review with CMO' : 'Continue routine monitoring';
  const logMedication = () => { const value = medication.trim(); if (value) { onAudit('medication_reported', `Operator-reported medication: ${value}; no interaction check performed`); setMedication(''); } };
  const warningEvidence = catalog.warnings_and_contraindications.slice(0, 4);
  const medicationEvidence = catalog.medication_evidence.slice(0, 3);
  const radiation = mission.space_metrics.radiation_msv;
  const bone = mission.space_metrics.bone_density_pct;
  const muscle = mission.space_metrics.muscle_strength_pct;
  return <aside className="panel right-rail" aria-label="Triage summary and activity log">
    <section className={`next-action next-action--${triage.urgency}`}>
      <div className="eyebrow">NEXT STEP · DECISION SUPPORT</div><h2>{next}</h2>
      <p>{triage.urgency === 'routine' ? 'No configured vital threshold is currently flagged.' : 'This is a demo alert. Recheck the measurement and use approved onboard procedures and crew medical authority.'}</p>
      <div className="autonomy"><b>COMMUNICATIONS / AUTONOMY</b><span>Deep-space link delay is {Math.round(mission.comms_delay_seconds / 60)} min one way. No autonomous treatment or evacuation authorization rules are loaded.</span></div>
    </section>
    <section className="rail-section"><div className="section-head compact"><h2>Warnings & source evidence</h2><span>{state.triage.flags.length} ACTIVE</span></div>
      {triage.flags.length ? triage.flags.map(flag => <div className={`rail-flag rail-flag--${flag.severity}`} key={flag.id}><b>{flag.severity.toUpperCase()} · {flag.label}</b><span>{flag.value} {flag.unit} — {flag.threshold}</span></div>) : <p className="muted">No threshold flags. Source cautions below remain unverified.</p>}
      <b className="evidence-group-title">Warnings / contraindications · unverified</b>
      {warningEvidence.map((item, index) => <details className="evidence-item" key={`warning-${item.source_id}-${item.page}-${index}`}><summary>PDF caution · p. {item.page}</summary><p>{item.excerpt}</p></details>)}
      <b className="evidence-group-title">Medication mentions · unverified</b>
      {medicationEvidence.map((item, index) => <details className="evidence-item" key={`medication-${item.source_id}-${item.page}-${index}`}><summary>PDF medication text · p. {item.page}</summary><p>{item.excerpt}</p></details>)}
      {!warningEvidence.length && !medicationEvidence.length && <p className="muted">No warning or medication evidence lines were extracted.</p>}
    </section>
    <section className="rail-section med-record"><label className="eyebrow" htmlFor="medication-reported">OPERATOR MEDICATION NOTE</label><div><input id="medication-reported" value={medication} onChange={e => setMedication(e.target.value)} placeholder="Record only; no interaction check" /><button onClick={logMedication} disabled={!medication.trim()}>Log</button></div><small>Medication is recorded as entered. No prescribing or drug-interaction logic is enabled.</small></section>
    <section className="rail-section mission-metrics"><div className="section-head compact"><h2>Mission health · Day {mission.day}</h2><span>{mission.environment}</span></div>
      <Metric label="CUMULATIVE RADIATION" value={`${radiation.value.toFixed(1)} mSv`} detail={`${radiation.daily_rate.toFixed(1)} mSv/day · ${radiation.status.toUpperCase()}`} />
      <Metric label="BONE / MUSCLE / MII" value={`${bone.value.toFixed(1)}% / ${muscle.value.toFixed(1)}% / ${mission.space_metrics.mii_score.value.toFixed(1)}%`} detail={`VO₂ max ${mission.space_metrics.vo2_max.value.toFixed(1)} ml/min/kg`} />
    </section>
    <section className="rail-section audit-log" id="audit-log"><div className="section-head compact"><h2>MET activity log</h2><span>{state.audit.length} EVENTS</span></div>
      {state.audit.length ? [...state.audit].reverse().slice(0, 8).map((event, i) => <div className="audit-event" key={`${event.timestamp_utc}-${i}`}><b>MET {event.met} · {event.type.replace(/_/g, ' ')}</b><span>{event.detail}</span></div>) : <p className="muted">Vitals, symptoms, checklist actions, and operator medication notes will appear here.</p>}
    </section>
  </aside>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="mission-metric"><small>{label}</small><b>{value}</b><span>{detail}</span></div>;
}
