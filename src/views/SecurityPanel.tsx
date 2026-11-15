import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { AlertTriangle, KeyRound, LockKeyhole, RefreshCw, ShieldAlert } from 'lucide-react';
import { api, expireSession, TOKEN_KEY } from '../lib';
import '../security.css';

type SecurityStatus = {
  demo_password_in_use: boolean;
  min_password_length: number;
  max_password_length: number;
};

/** Self-contained Settings section. Identity and credential status come from the server session. */
export function SecurityPanel() {
  const [status, setStatus] = useState<SecurityStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusError, setStatusError] = useState('');
  const [formError, setFormError] = useState('');
  const [formNotice, setFormNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    try {
      const value = await api<SecurityStatus>('/auth/security', { cache: 'no-store' });
      setStatus(value);
      setStatusError('');
    } catch (error) {
      setStatus(null);
      setStatusError(error instanceof Error ? error.message : 'Security status is unavailable.');
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void loadStatus(); }, [loadStatus]);

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFormError('');
    setFormNotice('');
    const form = event.currentTarget;
    const fields = new FormData(form);
    const currentPassword = String(fields.get('current_password') ?? '');
    const newPassword = String(fields.get('new_password') ?? '');
    const confirmation = String(fields.get('confirm_password') ?? '');
    if (newPassword !== confirmation) {
      setFormError('The new passphrase and confirmation do not match.');
      return;
    }
    setBusy(true);
    const requestToken = sessionStorage.getItem(TOKEN_KEY);
    try {
      await api('/auth/password', {
        method: 'POST',
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      // The server revoked the changed account's sessions. Do not sign out a
      // different session that may have started while this request was pending.
      if (expireSession(requestToken, { reason: 'password-changed', message: 'Password changed. All sessions were signed out; sign in again with your new passphrase.' })) {
        form.reset();
      } else if (mounted.current) {
        setFormNotice('The passphrase change for the previous account completed. That account’s sessions were revoked. Your current local session was not cleared; if it belongs to that account, you may need to sign in again.');
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Password change failed.';
      if (mounted.current) setFormError(`${message} If the connection was interrupted, the change may have completed; sign in with the new passphrase if your session no longer works.`);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  return <section className="security-panel" aria-labelledby="security-heading">
    <div className="security-heading">
      <div className="security-heading-icon" aria-hidden="true"><LockKeyhole size={18} /></div>
      <div><span className="security-kicker">LOCAL ACCESS / ACCOUNT</span><h2 id="security-heading">Security &amp; passphrase</h2><p>Manage your own local account. The server verifies your current session.</p></div>
    </div>

    <div className="security-warning" role="note">
      <ShieldAlert size={19} aria-hidden="true" />
      <div><strong>Demonstration environment — not production security</strong><p>Fresh demo accounts use publicly documented credentials. SQLite data and backups are not encrypted by the application. This is a simulation only, not a medical device or approved clinical guidance; never enter real personal or health information.</p></div>
    </div>

    <div className="security-grid">
      <div className="security-status-card">
        <div className="security-card-title"><span>ACCOUNT STATUS</span><button type="button" onClick={() => void loadStatus()} disabled={loading} aria-label="Refresh security status"><RefreshCw size={15} /></button></div>
        {loading && <p className="security-muted" role="status">Checking your account…</p>}
        {!loading && statusError && <p className="security-error" role="alert">{statusError}</p>}
        {!loading && status && <>
          <div className={status.demo_password_in_use ? 'security-status security-status-warning' : 'security-status'}>
            {status.demo_password_in_use ? <AlertTriangle size={18} aria-hidden="true" /> : <KeyRound size={18} aria-hidden="true" />}
            <div><strong>{status.demo_password_in_use ? 'Public demo password is still in use' : 'Public demo password is not in use'}</strong><p>{status.demo_password_in_use ? 'Anyone with the published demo credentials can access this account. Change it before continuing any local experiment.' : 'This does not establish that the passphrase or this prototype is secure.'}</p></div>
          </div>
          <ul className="security-facts"><li>Local password authentication with expiring sessions</li><li>Repeated failed sign-ins are temporarily limited per account</li><li>Successful password change signs out every session for this account</li></ul>
        </>}
      </div>
      <form className="security-form" onSubmit={changePassword}>
        <div className="security-card-title"><span>CHANGE YOUR PASSPHRASE</span><KeyRound size={16} aria-hidden="true" /></div>
        <label>Current password<input name="current_password" type="password" autoComplete="current-password" maxLength={256} required disabled={busy} /></label>
        <label>New passphrase<input name="new_password" type="password" autoComplete="new-password" minLength={12} maxLength={256} required disabled={busy} aria-describedby="security-passphrase-help" /></label>
        <label>Confirm new passphrase<input name="confirm_password" type="password" autoComplete="new-password" minLength={12} maxLength={256} required disabled={busy} /></label>
        <p id="security-passphrase-help" className="security-hint">Use 12–256 characters and choose a long, unique passphrase. The public demo password, a limited set of common values, repeated single characters, and your current password are rejected. This is not a comprehensive check for common or compromised passwords. Do not start or end with whitespace or use control characters.</p>
        <p className="security-signout-note">After a successful change, all sessions, including this one, are signed out. You must sign in again with the new passphrase.</p>
        {formError && <div className="security-form-error" role="alert">{formError}</div>}
        {formNotice && <div className="security-signout-note" role="status">{formNotice}</div>}
        <button className="security-submit" type="submit" disabled={busy}>{busy ? 'Updating…' : 'Change passphrase & sign out'}</button>
      </form>
    </div>
  </section>;
}
