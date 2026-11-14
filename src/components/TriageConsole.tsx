import { useMemo, useState } from 'react';
import { REGIONS, SYMPTOMS, type MedicalCatalog, type ProtocolCandidate } from '../data';

const reviewed = (p: ProtocolCandidate) => ['clinically_verified', 'verified'].includes(p.verification_status);

export function TriageConsole({ catalog, symptoms, onToggleSymptom, region, onRegion, query, onQuery, onAudit }: {
  catalog: MedicalCatalog; symptoms: string[]; onToggleSymptom: (id: string) => void;
  region: string; onRegion: (value: string) => void; query: string; onQuery: (value: string) => void;
  onAudit: (type: string, detail: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [stepsDone, setStepsDone] = useState<string[]>([]);
  const results = useMemo(() => catalog.protocols.filter(p => {
    const text = `${p.title} ${p.section} ${p.evidence_excerpt}`.toLowerCase();
    const regionMatch = region === 'all' || p.region === region;
    const queryMatch = !query.trim() || text.includes(query.trim().toLowerCase());
    return regionMatch && queryMatch;
  }).slice(0, 24), [catalog.protocols, region, query]);
  const toggleStep = (p: ProtocolCandidate, stepId: string, text: string) => {
    const isDone = stepsDone.includes(stepId);
    setStepsDone(isDone ? stepsDone.filter(id => id !== stepId) : [...stepsDone, stepId]);
    if (!isDone) onAudit('checklist_step', `${p.title}: ${text}`);
  };
  return <section className="panel triage-console" id="protocols">
    <div className="section-head"><div><span className="eyebrow">MEDICAL KNOWLEDGE BASE · LOCAL PDFS</span><h1>Symptom triage</h1></div><span className="source-count">{catalog.protocols.length} SOURCE CANDIDATES</span></div>
    <div className="safety-banner"><b>REVIEW REQUIRED</b><span>Extracted checklist text is not approved clinical guidance. No differential diagnosis or executable checklist is available until a qualified reviewer verifies and maps the source pages.</span></div>
    <div className="symptom-label">Select reported symptoms</div>
    <div className="symptom-pills">{SYMPTOMS.map(item => <button key={item.id} aria-pressed={symptoms.includes(item.id)} className={symptoms.includes(item.id) ? 'selected' : ''} onClick={() => onToggleSymptom(item.id)}>{item.label}</button>)}</div>
    <div className="protocol-tools"><div className="region-tabs">{REGIONS.map(item => <button className={region === item.id ? 'active' : ''} key={item.id} onClick={() => onRegion(item.id)}>{item.label}</button>)}</div><input aria-label="Search local source text" placeholder="Search extracted checklist text…" value={query} onChange={e => onQuery(e.target.value)} /></div>
    <div className="source-summary">{catalog.sources.map(source => <div key={source.id}><b>{source.file_name}</b><span>{source.page_count} pages · SHA-256 {source.sha256.slice(0, 12)}…</span></div>)}</div>
    <div className="protocol-list">
      {results.length ? results.map((p: ProtocolCandidate) => <article className="protocol-card" key={p.id}>
        <button className="protocol-title" aria-expanded={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)}><span><small>{p.section} · PDF p. {p.page_start}{p.printed_page_reference ? ` · ${p.printed_page_reference}` : ''}</small><b>{p.title}</b></span><em>UNVERIFIED SOURCE EXTRACT</em></button>
        {open === p.id && <div className="protocol-detail"><div className="unverified-label">Raw source excerpt · verify against cited PDF before any operational use</div><pre>{p.evidence_excerpt || 'No page excerpt captured.'}</pre>{reviewed(p) ? <div>{p.steps.map(step => <label className="approved-step" key={step.id}><input type="checkbox" checked={stepsDone.includes(step.id)} onChange={() => toggleStep(p, step.id, step.text)} />{step.text}</label>)}</div> : <p className="empty-approved">No approved symptom mapping, differential, tests, medication directions, or checklist steps are currently loaded.</p>}</div>}
      </article>) : <p className="empty-approved">No extracted source page matches this search. Clear the search or choose another section.</p>}
    </div>
    <div className="catalog-note">{catalog.notice}</div>
  </section>;
}
