import { SOLAR, STATUS_COLOR, type SolarLevel } from '../data';
import { Sparkline, StatusPill } from './ui';

const BAR_COLORS = ['var(--ok)', 'var(--watch)', 'var(--act)'];
const barColor = (i: number) => BAR_COLORS[i < 4 ? 0 : i < 8 ? 1 : 2];

export function RightRail({ solar }: { solar: SolarLevel }) {
  const s = SOLAR[solar];
  const storm = solar === 'storm';
  const warnings = s.warning ? 3 : 2;

  return (
    <aside className="rail" aria-label="Next step, warnings and cabin">
      <section className={`next-step${storm ? ' next-step--act' : ''}`}>
        <div className="next-step-head">
          <span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
            NEXT STEP
          </span>
          <span>~2 MIN</span>
        </div>
        <div className="next-step-body">
          <div><span className="display">{s.nextTitle}</span><span>{s.nextWhy}</span></div>
          <button type="button" className="btn btn--dark">Start</button>
        </div>
      </section>

      <section className={`panel warnings ${storm ? 'panel--act' : 'panel--watch'}`}>
        <div className="warnings-head">
          <span style={{ color: storm ? 'var(--act)' : 'var(--watch)' }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true"><path d="M12 3l9 16H3z" /><path d="M12 10v4M12 17h.01" /></svg>
            WARNINGS · {warnings}
          </span>
          <button type="button" className="btn--ghost">Acknowledge</button>
        </div>
        {s.warning && (
          <p className={`warning${storm ? ' warning--act' : ''}`} style={{ color: 'var(--text-soft)' }}>
            <strong style={{ color: STATUS_COLOR[s.status] }}>{s.warning.title}.</strong> <span>{s.warning.text}</span>
          </p>
        )}
        <p className="warning"><strong>Sleep low.</strong> 3 nights under 6 h.</p>
        <p className="warning"><strong>Light low.</strong> Get morning light.</p>
      </section>

      <section className="panel metric">
        <div className="eyebrow"><span>RADIATION</span><StatusPill status={s.radStatus} label={s.radLabel} /></div>
        <div className="metric-line"><span className="display">{s.radToday}</span><span>mSv today</span></div>
        <div className="metric-foot"><span>Mission total 15.9 mSv</span><span>38% of budget</span></div>
        <div className="bar"><i style={{ width: '38%' }} /></div>
      </section>

      <section className="panel metric">
        <div className="eyebrow"><span>CABIN CO₂</span><StatusPill status="ok" label="NORMAL" /></div>
        <div className="metric-line"><span className="display">2.6</span><span>mmHg · limit 4.0</span></div>
        <div className="spark">
          <Sparkline viewBox="0 0 280 34" height={28} baseline={6} guide="#7f1d1d" points="0,20 40,19 80,21 120,17 160,18 200,16 240,19 280,18" />
        </div>
      </section>

      <section className="panel metric">
        <div className="eyebrow"><span>SOLAR ACTIVITY</span><StatusPill status={s.status} label={s.label} /></div>
        <div className="headline">{s.line}</div>
        <div className="solar-bars" aria-hidden="true">
          {Array.from({ length: 12 }, (_, i) => <i key={i} style={i < s.level ? { background: barColor(i) } : undefined} />)}
        </div>
      </section>
    </aside>
  );
}
