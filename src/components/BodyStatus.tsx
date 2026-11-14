import { useState } from 'react';
import bodyUrl from '../assets/body.png';
import { REGIONS, STATUS_COLOR, type RegionId } from '../data';
import { Dot, StatusPill } from './ui';

/** Positions inside the 400x510 body stage. */
const LAYOUT: Record<RegionId, { hot: [number, number]; call: [number, number, number]; line: [number, number, number, number] }> = {
  head: { hot: [178, 8], call: [266, 4, 134], line: [200, 30, 266, 27] },
  arms: { hot: [241, 116], call: [266, 56, 134], line: [263, 138, 300, 102] },
  chest: { hot: [195, 104], call: [0, 38, 122], line: [217, 126, 122, 61] },
  stomach: { hot: [178, 166], call: [266, 268, 134], line: [200, 188, 266, 291] },
  legs: { hot: [145, 274], call: [0, 350, 122], line: [167, 296, 122, 373] },
};

export function BodyStatus({ scale }: { scale: number }) {
  const [selected, setSelected] = useState<RegionId>('head');
  const current = REGIONS.find((r) => r.id === selected)!;

  return (
    <section className="panel body-panel">
      <div className="eyebrow">
        <span>BODY STATUS · ALEX RIVERA</span>
        <span className="legend"><StatusPill status="ok" /><StatusPill status="watch" /><StatusPill status="act" /></span>
      </div>

      <div className="body-stage" style={{ width: 400 * scale, height: 510 * scale }}>
        <div className="body-scale" style={{ transform: `scale(${scale})` }}>
          <div className="body-glow" />
          <svg className="body-layer" width="400" height="510" viewBox="0 0 400 510" aria-hidden="true">
            <ellipse cx="200" cy="484" rx="132" ry="14" fill="rgba(92,200,255,0.06)" stroke="#5CC8FF" strokeOpacity="0.35" />
            <ellipse cx="200" cy="484" rx="92" ry="10" fill="none" stroke="#5CC8FF" strokeOpacity="0.25" strokeDasharray="3 5" />
            <ellipse cx="200" cy="484" rx="52" ry="5.5" fill="rgba(92,200,255,0.10)" />
            <path d="M1 18V1h17M382 1h17v17M399 492v17h-17M18 509H1v-17" fill="none" stroke="#5CC8FF" strokeOpacity="0.5" strokeWidth="1.5" />
          </svg>
          <img className="body-img" src={bodyUrl} alt="Front view of the crew member's body" />
          <div className="scan" />
          <svg className="body-layer" width="400" height="510" viewBox="0 0 400 510" aria-hidden="true">
            {REGIONS.map((r) => {
              const [x1, y1, x2, y2] = LAYOUT[r.id].line;
              return <line key={r.id} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#7FA6D9" strokeOpacity="0.55" strokeWidth="1" />;
            })}
          </svg>

          {REGIONS.map((r) => (
            <button
              key={`h-${r.id}`} type="button" className="hotspot" aria-label={r.name[0] + r.name.slice(1).toLowerCase()}
              aria-pressed={r.id === selected}
              style={{ left: LAYOUT[r.id].hot[0], top: LAYOUT[r.id].hot[1] }} onClick={() => setSelected(r.id)}
            >
              {r.status === 'watch' && <span className="ping" style={{ background: STATUS_COLOR[r.status] }} />}
              <span className="ring" />
              <span className="dot" style={{ background: STATUS_COLOR[r.status] }} />
            </button>
          ))}

          {REGIONS.map((r) => {
            const [x, y, w] = LAYOUT[r.id].call;
            return (
              <button key={`c-${r.id}`} type="button" className="callout" aria-pressed={r.id === selected}
                style={{ left: x, top: y, width: w }} onClick={() => setSelected(r.id)}>
                <span className="callout-head"><span>{r.name}</span><StatusPill status={r.status} size={6} /></span>
                <span className="callout-text">{r.summary}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="region-note" aria-live="polite">
        <div>
          <span className="region-title">
            {current.name}
            <span className="status" style={{ color: STATUS_COLOR[current.status] }}>
              <Dot color="currentColor" />{current.status === 'watch' ? 'WATCH' : 'OK'}
            </span>
          </span>
          <p>{current.note}</p>
        </div>
        <button type="button" className="btn btn--light">Report a symptom</button>
      </div>
    </section>
  );
}
