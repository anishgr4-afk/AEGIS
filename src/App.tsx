import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, AlertCircle, AlertTriangle, ArrowRight, Bell, BookOpen, ChevronRight, CircleHelp, ClipboardList, Cross, LayoutDashboard, LifeBuoy, LogOut, Menu, Radio, Radiation, Search, Settings, Shield, UserRound, Wind, X } from 'lucide-react';
import { api, expireSession, EXPIRY_KEY, formatTime, json, Snapshot, TOKEN_KEY, User } from './lib';
import { AlertsView, BodyView, DashboardView, EnvironmentView, RadiationView, TrendsView } from './views/CoreViews';
import { EarthLinkView, EmergencyView, LogView, MedicalView, SettingsView } from './views/OperationsViews';
import { CommandPalette, type CommandItem } from './views/CommandPalette';
import { OperationsDesk } from './views/OperationsDesk';
import { SecurityPanel } from './views/SecurityPanel';
import { startSnapshotPolling } from './views/snapshotPolling';
import { hasOmittedActiveAlerts, limitedWindows, snapshotWindow } from './views/snapshotWindow';
import './motion.css';

type Module = 'Dashboard' | 'Body' | 'Trends' | 'Earth Link' | 'Log' | 'Operations Desk' | 'Medical' | 'Radiation' | 'Environment' | 'Alerts' | 'Emergency' | 'Security' | 'Settings';
// Shared by the sidebar and command palette.
const navigation: (CommandItem & { name: Module })[] = [
  { name: 'Dashboard', icon: LayoutDashboard, group: 'MISSION', description: 'Mission and crew overview', keywords: 'overview home' },
  { name: 'Body', icon: UserRound, group: 'MISSION', description: 'Crew profiles and body telemetry', keywords: 'crew health vital' },
  { name: 'Trends', icon: Activity, group: 'MISSION', description: 'Explore measurement and alert history', keywords: 'charts history' },
  { name: 'Earth Link', icon: Radio, group: 'MISSION', description: 'Communications with Earth', keywords: 'message sync communications' },
  { name: 'Log', icon: BookOpen, group: 'MISSION', description: 'Recorded mission activity', keywords: 'event history' },
  { name: 'Operations Desk', icon: ClipboardList, group: 'MISSION', description: 'Tasks and shift handovers', keywords: 'operations tasks mission' },
  { name: 'Medical', icon: Cross, group: 'CREW & SYSTEMS', description: 'Medical records and supplies', keywords: 'care inventory' },
  { name: 'Radiation', icon: Radiation, group: 'CREW & SYSTEMS', description: 'Radiation exposure and records', keywords: 'dose exposure' },
  { name: 'Environment', icon: Wind, group: 'CREW & SYSTEMS', description: 'Cabin and environmental readings', keywords: 'cabin air oxygen temperature' },
  { name: 'Alerts', icon: Bell, group: 'CREW & SYSTEMS', description: 'Review mission alerts', keywords: 'warning critical' },
  { name: 'Emergency', icon: LifeBuoy, group: 'CREW & SYSTEMS', description: 'Emergency response workspace', keywords: 'incident checklist' },
  { name: 'Security', icon: Shield, group: 'SYSTEM', description: 'Protect your local account', keywords: 'access audit password' },
  { name: 'Settings', icon: Settings, group: 'SYSTEM', description: 'Console and account settings', keywords: 'configuration preferences' },
];

function Mark() { return <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>; }

function Login({ onLogin, expired, notice }: { onLogin: (user: User) => void; expired: boolean; notice: string }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const result = await api<{ token: string; user: User; expires_at?: string }>('/auth/login', json({ username, password }));
      if (!result.token || !result.user) throw new Error('The server did not provide a valid session.');
      sessionStorage.setItem(TOKEN_KEY, result.token); if (result.expires_at) sessionStorage.setItem(EXPIRY_KEY, result.expires_at); else sessionStorage.removeItem(EXPIRY_KEY); onLogin(result.user);
    } catch (e) { setError(e instanceof Error ? e.message : 'Sign in failed.'); } finally { setBusy(false); }
  }
  return <div className="login-page"><div className="login-top"><div className="brand"><Mark /><div><strong>CHECKPOINT</strong><small>CREW HEALTH & MISSION INTELLIGENCE</small></div></div><span className="top-system"><span className="dot green-bg" /> SIMULATION ENVIRONMENT</span></div>
    <div className="login-layout"><div className="login-intro"><div className="eyebrow"><span className="accent-line" /> MISSION SYSTEMS / 01</div><h1>Every signal<br /><em>matters.</em></h1><p>An autonomous mission intelligence console for the journey beyond Earth.</p><div className="login-orbit" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit-planet" /><div className="orbit-dot" /></div><div className="login-caption">DEEP SPACE OPERATIONS <span>·</span> CREW HEALTH MONITORING</div></div>
    <div className="login-card"><div className="eyebrow cyan">SECURE ACCESS <span> / </span> CHECKPOINT</div><h2>Sign in to the console</h2><p>Authenticate with a local demo account to continue.</p>{expired && <div className="notice amber-notice" role="status">{notice || 'Your session expired or is no longer valid. Please sign in again.'}</div>}{error && <div className="notice error-notice" role="alert">{error}</div>}<form onSubmit={submit}><label>USERNAME<input autoComplete="username" required value={username} onChange={e => setUsername(e.target.value)} placeholder="Enter username" /></label><label>PASSWORD<input autoComplete="current-password" type="password" required value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter password" /></label><button className="btn btn-primary login-submit" disabled={busy}>{busy ? 'AUTHENTICATING…' : 'ACCESS CONSOLE'} <ArrowRight size={16} /></button></form><div className="demo-credentials"><strong>DEMO ACCESS</strong><p>Users: <b>commander</b>, <b>medical</b>, <b>operator</b>, <b>admin</b><br />Initial password: <b>checkpoint-demo</b> (only until changed)</p><p>These public demo credentials are not secure. Change your passphrase in Security.</p></div></div></div>
    <div className="login-footer"><Shield size={16} /><span><strong>SIMULATION ONLY — NOT FOR CLINICAL USE.</strong> Do not enter real personal or health information. All decisions require human review.</span></div></div>;
}

function App() {
  const [user, setUser] = useState<User | null>(null); const [data, setData] = useState<Snapshot | null>(null);
  const [module, setModule] = useState<Module>('Dashboard'); const [loading, setLoading] = useState(true); const [snapshotLoading, setSnapshotLoading] = useState(false);
  const [error, setError] = useState(''); const [expired, setExpired] = useState(false); const [menuOpen, setMenuOpen] = useState(false); const [selectedCrew, setSelectedCrew] = useState('');
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [authNotice, setAuthNotice] = useState('');
  const poller = useRef<ReturnType<typeof startSnapshotPolling<Snapshot>> | null>(null);
  useEffect(() => {
    const expire = (event: Event) => { const detail = event instanceof CustomEvent ? event.detail : undefined; if (typeof detail?.message === 'string') setAuthNotice(detail.message); setUser(null); setData(null); setPaletteOpen(false); setExpired(true); setLoading(false); };
    window.addEventListener('checkpoint:session-expired', expire);
    let active = true;
    const restoreToken = sessionStorage.getItem(TOKEN_KEY);
    if (restoreToken) api<User>('/auth/session')
      .then(value => { if (active && sessionStorage.getItem(TOKEN_KEY) === restoreToken) setUser(value); })
      .catch(() => { if (active) expireSession(restoreToken); })
      .finally(() => { if (active && (!sessionStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY) === restoreToken)) setLoading(false); });
    else setLoading(false);
    return () => { active = false; window.removeEventListener('checkpoint:session-expired', expire); };
  }, []);
  useEffect(() => {
    if (!user) return;
    const sessionToken = sessionStorage.getItem(TOKEN_KEY);
    const expiry = Date.parse(sessionStorage.getItem(EXPIRY_KEY) || '');
    if (!Number.isFinite(expiry)) return;
    const expire = () => { expireSession(sessionToken); };
    const remaining = expiry - Date.now();
    if (remaining <= 0) { expire(); return; }
    const timer = window.setTimeout(expire, Math.min(remaining, 2147483647));
    return () => window.clearTimeout(timer);
  }, [user]);
  const refresh = useCallback(() => poller.current?.refresh() ?? Promise.reject(new Error('Mission data is not available.')), []);
  useEffect(() => {
    if (!user) return;
    const sessionToken = sessionStorage.getItem(TOKEN_KEY);
    let lastSnapshot: string | null = null;
    let firstResult = true;
    setSnapshotLoading(true); setError('');
    const current = startSnapshotPolling<Snapshot>({
      request: signal => api<Snapshot>('/snapshot', { signal, cache: 'no-store' }),
      isVisible: () => document.visibilityState === 'visible',
      onManualLoading: setSnapshotLoading,
      onData: next => {
        if (sessionStorage.getItem(TOKEN_KEY) !== sessionToken) return;
        // Keep the existing object (and all mounted form state) when nothing changed.
        const fingerprint = JSON.stringify(next);
        if (fingerprint !== lastSnapshot) { lastSnapshot = fingerprint; setData(next); }
        if (firstResult) { firstResult = false; setSnapshotLoading(false); }
        setError('');
      },
      onError: err => {
        if (sessionStorage.getItem(TOKEN_KEY) !== sessionToken) return;
        if (firstResult) { firstResult = false; setSnapshotLoading(false); }
        setError(err.message);
      },
    });
    poller.current = current;
    const visibilityChanged = () => current.visibilityChanged();
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => {
      document.removeEventListener('visibilitychange', visibilityChanged);
      current.stop();
      if (poller.current === current) poller.current = null;
    };
  }, [user]);
  useEffect(() => {
    if (!user || loading) return;
    const shortcut = (event: KeyboardEvent) => {
      // Intercept only the explicit shortcut, never ordinary typing, IME or modified arrows.
      if (!event.defaultPrevented && !event.isComposing && !event.repeat && (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen(open => !open);
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [user, loading]);
  const navigate = (value: string, crewId?: string) => { if (crewId) setSelectedCrew(crewId); setModule(value as Module); setMenuOpen(false); window.scrollTo({ top: 0, behavior: 'auto' }); };
  async function logout() {
    poller.current?.stop(); poller.current = null;
    const request = api('/auth/logout', { method: 'POST' }).catch(() => { /* local session is still cleared */ });
    sessionStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(EXPIRY_KEY); setUser(null); setData(null); setPaletteOpen(false); setExpired(false); setModule('Dashboard');
    await request;
  }
  if (loading) return <div className="splash"><Mark /><span>INITIALIZING CHECKPOINT…</span></div>;
  if (!user) return <Login expired={expired} notice={authNotice} onLogin={u => { setExpired(false); setAuthNotice(''); setUser(u); }} />;
  const activeAlerts = data?.alerts.filter(a => !a.resolved) || [];
  const activeCount = data ? snapshotWindow(data, 'alerts')?.active ?? activeAlerts.length : 0;
  const limited = data ? limitedWindows(data) : [];
  const common = data ? { user, data, refresh } : null;
  const view = !common ? null : module === 'Dashboard' ? <DashboardView {...common} navigate={navigate} /> : module === 'Body' ? <BodyView {...common} initialCrewId={selectedCrew} /> : module === 'Trends' ? <TrendsView {...common} /> : module === 'Radiation' ? <RadiationView {...common} /> : module === 'Environment' ? <EnvironmentView {...common} /> : module === 'Alerts' ? <AlertsView {...common} /> : module === 'Earth Link' ? <EarthLinkView {...common} /> : module === 'Medical' ? <MedicalView {...common} /> : module === 'Emergency' ? <EmergencyView {...common} /> : module === 'Log' ? <LogView {...common} /> : module === 'Operations Desk' ? <OperationsDesk {...common} /> : module === 'Security' ? <><div className="page-heading"><div><div className="eyebrow"><span className="accent-line" /> SYSTEM / ACCESS CONTROL</div><h1>Security</h1><p>Review your local account and update your passphrase.</p></div></div><SecurityPanel /></> : <SettingsView {...common} />;
  return <div className="app-shell"><header className="app-header"><button className="mobile-menu icon-button" aria-label={menuOpen ? 'Close navigation' : 'Open navigation'} onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={20} /> : <Menu size={20} />}</button><div className="brand"><Mark /><div><strong>CHECKPOINT</strong><small>CREW HEALTH & MISSION INTELLIGENCE</small></div></div><div className="header-center"><span className="header-tag"><span className={`dot ${data && !error ? 'green-bg' : 'amber-bg'}`} /> {data && !error ? 'LOCAL SERVICE AVAILABLE' : 'SERVICE UNAVAILABLE'}</span><span className="header-divider" /><span>MISSION DAY <b>{data?.mission?.mission_day ?? '—'}</b></span><span className="header-divider" /><span>{data?.mission?.location || 'LOCATION UNAVAILABLE'}</span></div><div className="header-right"><button id="command-palette-trigger" type="button" className="command-trigger" aria-label="Search console modules" aria-haspopup="dialog" aria-expanded={paletteOpen} aria-keyshortcuts="Control+K Meta+K" title="Search console modules (Ctrl or Command + K)" onClick={() => setPaletteOpen(true)}><Search size={16} /><span>Search console</span><kbd>Ctrl / ⌘ K</kbd></button><button className="header-alert icon-button" aria-label={`${activeCount} active alerts`} onClick={() => navigate('Alerts')}><Bell size={17} />{activeCount > 0 && <span className="alert-count">{activeCount}</span>}</button><span className="avatar">{(user.display_name || user.username).split(' ').map(s => s[0]).slice(0, 2).join('').toUpperCase()}</span><div className="user-label"><strong>{user.display_name || user.username}</strong><small>{user.role}</small></div><button className="icon-button logout" onClick={logout} title="Sign out" aria-label="Sign out"><LogOut size={17} /></button></div></header>
    <div className="workspace"><aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}><div className="sidebar-heading">MISSION CONTROL <span>V 1.0</span></div><nav aria-label="Main navigation">{navigation.map(({ name, icon: Icon, group, pending }, index) => <div key={name}>{(index === 0 || navigation[index - 1].group !== group) && <div className="nav-group">{group}</div>}<button className={`nav-item ${module === name ? 'active' : ''}`} onClick={() => navigate(name)} aria-current={module === name ? 'page' : undefined}><Icon size={17} strokeWidth={1.7} /><span>{name}</span>{pending && <span className="nav-coming">PENDING</span>}{name === 'Alerts' && activeCount > 0 && <span className="nav-count">{activeCount}</span>}{module === name && <ChevronRight className="nav-arrow" size={14} />}</button></div>)}</nav><div className="sidebar-bottom"><div className="sidebar-status"><span className="dot green-bg" /> LOCAL CONSOLE <b>ACTIVE</b></div><small>CHECKPOINT // SIMULATION<br />NOT A MEDICAL DEVICE</small></div></aside>
    {menuOpen && <button className="mobile-overlay" onClick={() => setMenuOpen(false)} aria-label="Close navigation" />}
    <main className="main-content"><div className="safety-strip"><AlertTriangle size={15} /><span><strong>SIMULATION ONLY / NOT FOR CLINICAL USE</strong> — Demo data and guidance require human review. Do not enter real health information.</span></div><div className="breadcrumb">CHECKPOINT <ChevronRight size={12} /> <span>{module.toUpperCase()}</span><span className="breadcrumb-right">{snapshotLoading ? 'UPDATING…' : data ? `LAST SYNC ${formatTime(data.mission?.last_sync)}` : 'AWAITING DATA'}</span></div>{error && <div className="notice error-notice" role="alert"><AlertCircle size={16} /> {error} <button className="text-button" onClick={() => refresh().catch(() => {})}>Retry</button></div>}{limited.length > 0 && <div className="notice info-notice" role="status">Snapshot window limited (returned/total): {limited.join(', ')}. History, lists, and totals based on visible records may be incomplete.</div>}{data && hasOmittedActiveAlerts(data) && <div className="notice error-notice" role="alert">Some active alerts are not shown in this snapshot. Individual alert statuses and severities may be incomplete; review the full alert history via the paginated /api/alerts endpoint.</div>}{!data ? <div className="loading-panel">{snapshotLoading ? 'LOADING MISSION DATA…' : 'Mission data unavailable. Use Retry above.'}</div> : view}</main></div>
    <footer className="app-footer"><span><span className="dot green-bg" /> LOCAL SESSION ACTIVE</span><span>CHECKPOINT / SIMULATED MARS TRANSIT</span><span><CircleHelp size={12} /> HUMAN REVIEW REQUIRED</span></footer>
    {paletteOpen && <CommandPalette items={navigation} current={module} onClose={() => setPaletteOpen(false)} onSelect={name => { setPaletteOpen(false); navigate(name); }} />}</div>;
}
export default App;
