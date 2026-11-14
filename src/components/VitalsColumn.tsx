import { Sparkline, StatusPill, Value } from './ui';
import { VITALS, type Vital } from '../data';

function VitalCard({ v }: { v: Vital }) {
  const watch = v.status === 'watch';
  return (
    <section className={`panel vital${watch ? ' panel--watch' : ''}`}>
      <div className="vital-head"><span>{v.name}</span><StatusPill status={v.status} /></div>
      <div className="vital-body">
        <div className="display vital-value"><Value unit={v.unit}>{v.value}</Value></div>
        <div className="vital-chart">
          {v.bars ? (
            <div className="bars7" aria-hidden="true">
              {v.bars.map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}
            </div>
          ) : (
            <Sparkline
              points={v.points!} baseline={v.baseline}
              line={watch ? '#fbbf24' : '#c5cdd9'} guide={watch ? '#4a3e1e' : '#2a3548'}
            />
          )}
        </div>
      </div>
      <div className="vital-note">{v.note}</div>
    </section>
  );
}

export function VitalsColumn() {
  return (
    <div className="vitals" aria-label="Vitals, last 7 days">
      {VITALS.map((v) => <VitalCard key={v.name} v={v} />)}
    </div>
  );
}
