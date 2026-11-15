export type User = {
  id: string;
  username: string;
  display_name: string;
  role: 'Commander' | 'Medical Officer' | 'Mission Operator' | 'Administrator' | string;
};

export type RecordBase = { id: string; created_at: string; updated_at: string; [key: string]: any };
export type Crew = RecordBase & { name: string; role: string; status: string; baseline?: string; medical_history?: string; current_health?: string; last_telemetry?: string };
export type Measurement = RecordBase & { crew_id: string; metric: string; value: number; unit: string; source: string; timestamp: string };
export type Radiation = RecordBase & { crew_id: string; value: number; unit: string; source: string; timestamp: string; event?: string };
export type Environment = RecordBase & { metric: string; value: number; unit: string; source: string; timestamp: string };
export type Alert = RecordBase & { entity_type: string; entity_id: string; metric: string; value: number | null; unit: string; rule: string; severity: string; detected_at: string; acknowledged: boolean; resolved: boolean; guidance?: string };
export type MissionEvent = RecordBase & { category: string; entity: string; detail: string; timestamp: string };
export type Settings = Record<string, any> & { mission_day?: number; location?: string; delay_minutes?: number; earth_connected?: boolean; last_sync?: string; thresholds?: Record<string, any> };
export type Snapshot = { mission: Settings & { radiation_total?: number }; crew: Crew[]; measurements: Measurement[]; radiation: Radiation[]; environment: Environment[]; alerts: Alert[]; events: MissionEvent[]; settings: Settings };
export type ViewProps = { user: User; data: Snapshot; refresh: () => Promise<void> };

export const TOKEN_KEY = 'checkpoint_token';
export const EXPIRY_KEY = 'checkpoint_expiry';

/** End only the session that initiated an operation, never a newer sign-in. */
export function expireSession(token: string | null, detail?: { reason: string; message: string }): boolean {
  if (!token || sessionStorage.getItem(TOKEN_KEY) !== token) return false;
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(EXPIRY_KEY);
  window.dispatchEvent(detail
    ? new CustomEvent('checkpoint:session-expired', { detail })
    : new Event('checkpoint:session-expired'));
  return true;
}

/** Same-origin API client. A 401 ends the originating session if it is still current. */
export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  let response: Response;
  try {
    response = await fetch(path.startsWith('/api') ? path : `/api${path.startsWith('/') ? path : `/${path}`}`, { ...options, headers });
  } catch {
    throw new Error('Unable to reach the local service. Check the connection and try again.');
  }
  // Invalidate before decoding the body so even a malformed 401 cannot leave
  // a genuinely expired session active.
  if (response.status === 401 && !path.includes('/auth/login')) expireSession(token);
  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === 'object' && body && 'error' in body ? String(body.error) : response.status === 403 ? 'Your role does not have permission for this action.' : `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return body as T;
}

export const pretty = (text: string | undefined | null) => (text || '—').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
export const formatTime = (value?: string | null) => {
  if (!value) return 'No update';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Invalid timestamp' : `${date.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' })} UTC`;
};
export const formatDate = (value?: string | null) => {
  if (!value) return 'Not available';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Invalid timestamp' : `${date.toLocaleString(undefined, { year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' })} UTC`;
};
export function freshness(value?: string | null, staleMinutes = 1440) {
  if (!value || Number.isNaN(new Date(value).getTime())) return { label: 'MISSING', tone: 'muted' };
  const age = Date.now() - new Date(value).getTime();
  if (age < -5 * 60_000) return { label: 'FUTURE TIMESTAMP', tone: 'amber' };
  if (age > staleMinutes * 60_000) return { label: `STALE > ${staleMinutes} MIN`, tone: 'amber' };
  return { label: 'CURRENT', tone: 'green' };
}
export const number = (value: unknown, digits = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—';
export const latest = <T extends { timestamp: string }>(records: T[]) => [...records].filter(r => r.timestamp && !Number.isNaN(Date.parse(r.timestamp))).sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))[0];
export const canManageCrew = (user: User) => ['Commander', 'Medical Officer', 'Administrator'].includes(user.role);
export const canRecord = (user: User) => ['Commander', 'Medical Officer', 'Mission Operator', 'Administrator'].includes(user.role);
export const canReviewAlerts = (user: User, entityType?: string) => entityType === 'crew' ? ['Medical Officer', 'Administrator'].includes(user.role) : ['Commander', 'Mission Operator', 'Administrator'].includes(user.role);
export const json = (data: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(data) });
export const localInputTime = () => {
  const date = new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};
