export function EarthLink({ commsDelaySeconds, day, date }: { commsDelaySeconds: number; day: number; date: string }) {
  const minutes = commsDelaySeconds / 60;
  const oneWay = Number.isInteger(minutes) ? String(minutes) : minutes.toFixed(1);
  const roundTrip = Math.round(minutes * 2);
  const progress = Math.min(100, Math.round(day / 115 * 100));
  return (
    <section className="panel earthlink" id="earth-link" aria-label="Earth link">
      <span className="earth-label">EARTH LINK</span>
      <div className="route">
        <span className="route-end"><i className="planet planet-earth" />EARTH</span>
        <div className="route-line"><span className="route-done" style={{ width: `${progress}%` }} /><i className="route-arrow" style={{ left: `${progress}%` }} /></div>
        <span className="route-end">MARS<i className="planet planet-mars" /></span>
      </div>
      <div className="stat"><small>MISSION DATE</small><span className="display">{date}</span></div>
      <div className="stat"><small>DATA TO EARTH</small><span className="display">{oneWay} min <em>one way</em></span></div>
      <div className="stat"><small>REPLY BACK</small><span className="display">{roundTrip} min</span></div>
      <div className="stat stat--report"><small><span>DAILY REPORT</span><b>{progress}%</b></small><div className="bar"><i style={{ width: `${progress}%`, background: 'var(--accent)' }} /></div></div>
    </section>
  );
}
