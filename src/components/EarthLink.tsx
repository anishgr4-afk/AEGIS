const PROGRESS = 39;

export function EarthLink() {
  return (
    <section className="panel earthlink" aria-label="Earth link">
      <span className="label">EARTH LINK</span>
      <div className="route">
        <span className="end">
          <svg width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="#2B63B8" /></svg>EARTH
        </span>
        <div className="route-line">
          <div className="dash" />
          <div className="done" style={{ width: `${PROGRESS}%` }} />
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" style={{ left: `${PROGRESS}%` }}>
            <path d="M2 2l12 6-12 6 3-6z" fill="#E6EBF2" />
          </svg>
        </div>
        <span className="end">
          MARS<svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="7" fill="#A8482C" /></svg>
        </span>
      </div>
      <div className="stat"><small>DISTANCE TO EARTH</small><span className="display">215.9<span className="unit">M km</span></span></div>
      <div className="stat"><small>DATA TO EARTH</small><span className="display">12 min<span className="unit">one way</span></span></div>
      <div className="stat"><small>REPLY BACK</small><span className="display">24 min</span></div>
      <div className="stat stat--report">
        <small><span>DAILY REPORT</span><b>64% · 11:42 LEFT</b></small>
        <div className="bar"><i style={{ width: '64%', background: 'var(--accent)' }} /></div>
      </div>
    </section>
  );
}
