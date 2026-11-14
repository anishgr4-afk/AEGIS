const NAV = [
  ['Dashboard', '#dashboard'],
  ['Body', '#vitals'],
  ['Trends', '#protocols'],
  ['Earth link', '#earth-link'],
  ['Log', '#audit-log'],
];

export function TopBar({ day, environment, simulated, onSimulationChange }: {
  day: number;
  environment: string;
  simulated: boolean;
  onSimulationChange: (enabled: boolean) => void;
}) {
  return (
    <header className="panel topbar">
      <div className="topbar-left">
        <a className="brand" href="#dashboard" aria-label="Checkpoint dashboard">
          <span className="brand-check" aria-hidden="true">✓</span><span>CHECKPOINT</span>
        </a>
        <nav className="nav" aria-label="Console">
          {NAV.map(([label, href], index) => <a key={label} href={href} aria-current={index === 0 ? 'page' : undefined}>{label}</a>)}
        </nav>
      </div>
      <div className="topbar-right">
        <span>DAY {day} · {environment.toUpperCase()}</span>
        <span>MET <em>{String(day).padStart(3, '0')}:00:00:00</em></span>
        <button className={`sim-toggle${simulated ? ' is-on' : ''}`} type="button" aria-pressed={simulated} onClick={() => onSimulationChange(!simulated)}>
          <span className="toggle-dot" /> SIMULATED DATA
        </button>
        <span className="user">
          <span className="user-name"><b>Alex Vance</b><small>Commander</small></span>
          <span className="avatar">AV</span>
        </span>
      </div>
    </header>
  );
}
