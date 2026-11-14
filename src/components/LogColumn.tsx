import { MEASUREMENTS } from '../data';

export function LogColumn() {
  return (
    <section className="panel log-panel">
      <div className="eyebrow eyebrow--soft"><span>EXERCISE RECORD</span><b>30 / 90 MIN</b></div>
      <div className="bar"><i style={{ width: '33%' }} /></div>
      <div className="next-up">
        <small>NEXT · 14:00</small>
        <span className="display">Resistance training</span>
        <span>45 min</span>
      </div>
      <div className="divider" />
      <div className="eyebrow eyebrow--soft"><span>NUTRITION · TODAY</span><b>MEAL CODE B-14</b></div>
      <div className="kcal">
        <span className="display">860<span className="unit"> / 2,800 kcal</span></span>
        <div className="bar"><i style={{ width: '31%' }} /></div>
      </div>
      <div className="macros">
        <div className="macro"><small>Protein</small><span className="display">38<span className="unit"> / 120 g</span></span></div>
        <div className="macro macro--watch"><small>Water</small><span className="display">0.6<span className="unit"> / 2.5 L</span></span></div>
        <div className="macro"><small>Vitamin D</small><span className="taken">Taken</span></div>
      </div>
      <div className="divider" />
      <div className="eyebrow"><span>LAST MEASUREMENTS</span><a className="see-all" href="#">See all</a></div>
      <div className="measurements">
        {MEASUREMENTS.map((m) => (
          <div className="measure" key={m.name}>
            <div><span className="name">{m.name}</span><span className="when">{m.when}</span></div>
            <div>
              <span className="display">{m.value}{m.unit && <span className="unit">{m.unit}</span>}</span>
              <span className={`age${m.due ? ' age--due' : ''}`}>{m.age}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
