const NAV = ['Dashboard', 'Body', 'Trends', 'Earth link', 'Log'];

export function TopBar() {
  return (
    <header className="panel topbar">
      <div className="topbar-left">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#5CC8FF" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="9" /><path d="M7.5 12l3 3 6-6" />
          </svg>
          <span>CHECKPOINT</span>
        </div>
        <nav className="nav" aria-label="Console">
          {NAV.map((n, i) => (
            <a key={n} href="#" aria-current={i === 0 ? 'page' : undefined}>{n}</a>
          ))}
        </nav>
      </div>
      <div className="topbar-right">
        <span>DAY 34 · MARS TRANSIT</span>
        <span>MET <em>034:07:42:16</em></span>
        <span className="tag">SIMULATED DATA</span>
        <span className="user">
          <span className="user-name"><b>Alex Rivera</b><small>Mission Specialist</small></span>
          <span className="avatar">AR</span>
        </span>
      </div>
    </header>
  );
}
