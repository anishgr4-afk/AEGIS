import { useRef, useState } from 'react';
import { Activity, AlertTriangle, Clipboard, FileText, Radio, Users } from 'lucide-react';
import type { MissionEvent, Snapshot } from '../lib';
import { formatDate, pretty } from '../lib';
import { snapshotWindow } from './snapshotWindow';
import '../briefing.css';

// Only these fixed, operational labels can enter the handover. Event details,
// entity IDs, medical categories, and free-form profile fields are never copied.
const HANDOVER_CATEGORIES: Record<string, string> = {
  communication: 'Communication',
  system: 'System',
  operations: 'Operations',
  security: 'Security',
  crew: 'Crew administration',
};

const validCount = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

function newestEvents(events: MissionEvent[]) {
  return [...events].sort((a, b) => {
    const aTime = Date.parse(a.timestamp);
    const bTime = Date.parse(b.timestamp);
    return (Number.isFinite(bTime) ? bTime : -Infinity) - (Number.isFinite(aTime) ? aTime : -Infinity);
  });
}

export function getBriefingData(data: Snapshot) {
  const alerts = data.alerts || [];
  const active = alerts.filter(alert => alert.resolved === false);
  const visibleCritical = active.filter(alert => alert.severity === 'CRITICAL');
  const visibleUnacknowledged = visibleCritical.filter(alert => alert.acknowledged === false).length;
  const statusesComplete = alerts.every(alert => typeof alert.resolved === 'boolean' && typeof alert.acknowledged === 'boolean' && typeof alert.severity === 'string');
  const window = snapshotWindow(data, 'alerts');
  const activeTotal = validCount(window?.active) ? window.active : undefined;
  const criticalTotal = validCount(window?.active_critical) ? window.active_critical : undefined;
  const unacknowledgedTotal = validCount(window?.unacknowledged_critical) ? window.unacknowledged_critical : undefined;
  const inconsistent = (activeTotal !== undefined && activeTotal < active.length)
    || (criticalTotal !== undefined && criticalTotal < visibleCritical.length)
    || (unacknowledgedTotal !== undefined && unacknowledgedTotal < visibleUnacknowledged)
    || (activeTotal !== undefined && criticalTotal !== undefined && criticalTotal > activeTotal)
    || (criticalTotal !== undefined && unacknowledgedTotal !== undefined && unacknowledgedTotal > criticalTotal);
  const omitted = (activeTotal !== undefined && activeTotal > active.length)
    || (criticalTotal !== undefined && criticalTotal > visibleCritical.length)
    || (unacknowledgedTotal !== undefined && unacknowledgedTotal > visibleUnacknowledged);
  const allActiveVisible = !inconsistent && !omitted && statusesComplete
    && (window?.truncated === false || (activeTotal !== undefined && activeTotal === active.length));
  const authoritativeCount = !inconsistent && unacknowledgedTotal !== undefined;
  const countIsTotal = authoritativeCount || allActiveVisible;
  const unacknowledged = authoritativeCount ? unacknowledgedTotal : visibleUnacknowledged;
  const coverage = inconsistent
    ? 'Alert-window totals are inconsistent with visible records. The full alert count cannot be verified.'
    : omitted
      ? 'The snapshot alert window omits active alerts. Individual alert records may be incomplete.'
      : !allActiveVisible
        ? 'Alert-window coverage is limited or unavailable; additional active alerts may be missing.'
        : '';

  const connected = data.mission?.earth_connected;
  const link = connected === true ? 'Connected' : connected === false ? 'Disconnected' : 'Unavailable';
  const delay = data.mission?.delay_minutes;
  const delayText = typeof delay === 'number' && Number.isFinite(delay) && delay >= 0
    ? `${delay} min one way` : 'Unavailable';
  const events = newestEvents(data.events || []);
  return {
    crewCount: (data.crew || []).length,
    unacknowledged,
    countIsTotal,
    coverage,
    link,
    delayText,
    lastSync: formatDate(data.mission?.last_sync),
    events,
    eventsTruncated: snapshotWindow(data, 'events')?.truncated === true,
  };
}

/** Build a point-in-time, non-medical handover without copying free-text records. */
export function buildHandover(data: Snapshot, generatedAt = new Date()) {
  const briefing = getBriefingData(data);
  const operationalEvents = briefing.events.filter(event => Object.prototype.hasOwnProperty.call(HANDOVER_CATEGORIES, event.category)).slice(0, 4);
  const eventLines = operationalEvents.length
    ? operationalEvents.map(event => `- ${HANDOVER_CATEGORIES[event.category]} · ${formatDate(event.timestamp)}`)
    : ['- No operational events available in this snapshot.'];
  return [
    'CHECKPOINT / SIMULATED MISSION HANDOVER',
    `Generated: ${formatDate(generatedAt.toISOString())}`,
    'Simulation only; not medical advice. Avoid real medical information.',
    '',
    `Crew profiles in snapshot: ${briefing.crewCount}`,
    `Unacknowledged critical alerts${briefing.countIsTotal ? '' : ' (visible snapshot only)'}: ${briefing.unacknowledged}`,
    ...(briefing.coverage ? [`Alert coverage: ${briefing.coverage}`] : []),
    `Simulated Earth Link: ${briefing.link}`,
    `Configured communication delay: ${briefing.delayText}`,
    `Last synchronization: ${briefing.lastSync}`,
    '',
    'Recent operational event categories (snapshot):',
    ...eventLines,
    ...(briefing.eventsTruncated ? ['Event history is limited to the snapshot window.'] : []),
    '',
    'Event details and medical records are intentionally excluded. Review the source console for context.',
  ].join('\n');
}

export function MissionBriefing({ data }: { data: Snapshot }) {
  const briefing = getBriefingData(data);
  const [handover, setHandover] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const previewRef = useRef<HTMLTextAreaElement>(null);

  function generate() {
    setHandover(buildHandover(data));
    setCopyStatus('Handover generated. Review it before sharing.');
  }

  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(handover);
      setCopyStatus('Copied to clipboard.');
    } catch {
      previewRef.current?.focus();
      previewRef.current?.select();
      setCopyStatus('Could not copy automatically. The text is selected; use your device’s copy command.');
    }
  }

  return (
    <section className="mb-panel" aria-labelledby="mb-heading">
      <div className="mb-topline">
        <div>
          <div className="mb-eyebrow"><span aria-hidden="true" /> MISSION CONTROL / HANDOVER</div>
          <h2 id="mb-heading">Mission briefing</h2>
          <p>A concise operational snapshot for the next shift.</p>
        </div>
        <span className="mb-simulation">SIMULATION ONLY</span>
      </div>

      <div className="mb-metrics">
        <div className="mb-metric"><span className="mb-metric-label"><Users size={14} aria-hidden="true" /> CREW PROFILES</span><strong>{briefing.crewCount}</strong><small>In this snapshot</small></div>
        <div className="mb-metric"><span className="mb-metric-label"><AlertTriangle size={14} aria-hidden="true" /> UNACKNOWLEDGED CRITICAL</span><strong className={briefing.unacknowledged ? 'mb-alert-value' : ''}>{briefing.unacknowledged}</strong><small>{briefing.countIsTotal ? 'All active alerts' : 'Visible snapshot only'}</small></div>
        <div className="mb-metric"><span className="mb-metric-label"><Radio size={14} aria-hidden="true" /> EARTH LINK</span><strong className="mb-word-value">{briefing.link}</strong><small>Configured delay · {briefing.delayText}</small></div>
        <div className="mb-metric"><span className="mb-metric-label"><Activity size={14} aria-hidden="true" /> LAST SYNC</span><strong className="mb-time-value">{briefing.lastSync}</strong><small>Simulated synchronization</small></div>
      </div>

      {briefing.coverage && <div className="mb-coverage"><AlertTriangle size={15} aria-hidden="true" /><span>{briefing.coverage}</span></div>}

      <div className="mb-lower">
        <div className="mb-events">
          <div className="mb-section-title"><span>RECENT MISSION EVENTS</span><small>SNAPSHOT / {Math.min(briefing.events.length, 4)} SHOWN</small></div>
          {briefing.events.length ? <ol>{briefing.events.slice(0, 4).map((event, index) => (
            <li key={event.id || index}>
              <span className="mb-event-dot" aria-hidden="true" />
              <div><strong>{pretty(event.category)}</strong><p>{event.detail || 'No event details recorded.'}</p></div>
              <time>{formatDate(event.timestamp)}</time>
            </li>
          ))}</ol> : <p className="mb-empty">No mission events in this snapshot.</p>}
          {briefing.eventsTruncated && <p className="mb-window-note">Event history is limited to the snapshot window.</p>}
        </div>
        <div className="mb-handover">
          <div className="mb-handover-icon"><FileText size={18} aria-hidden="true" /></div>
          <h3>Prepare a handover</h3>
          <p>Create a plain-text operational summary from this snapshot. Event details and medical records are excluded.</p>
          <button type="button" className="mb-button mb-button-primary" onClick={generate}><FileText size={15} aria-hidden="true" /> {handover ? 'Regenerate handover' : 'Generate handover'}</button>
        </div>
      </div>

      {handover && <div className="mb-preview">
        <div className="mb-preview-heading"><label htmlFor="mb-handover-text">HANDOVER PREVIEW</label><span>Point-in-time copy · regenerate for updates</span></div>
        <textarea id="mb-handover-text" ref={previewRef} readOnly value={handover} rows={9} spellCheck={false} />
        <div className="mb-preview-actions"><button type="button" className="mb-button mb-button-outline" onClick={() => void copy()}><Clipboard size={15} aria-hidden="true" /> Copy text</button><span role="status" aria-live="polite">{copyStatus}</span></div>
      </div>}
      <p className="mb-privacy">Simulated data may include fictional crew names. Avoid entering or sharing real medical information. Review text before sharing; nothing is sent automatically.</p>
    </section>
  );
}
