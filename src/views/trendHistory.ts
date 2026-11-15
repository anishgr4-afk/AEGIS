import type { Alert, Environment, Measurement, Radiation } from '../lib';

export const TREND_PAGE_SIZE = 500;
export const TREND_RECORD_CAP = 4000;
const BP_STREAM_CAP = TREND_RECORD_CAP / 2;

export type TrendRecord = Measurement | Radiation | Environment | Alert;
export type TrendSelection = { metric: string; crew: string; period: string; since?: string; until?: string };
export type TrendHistory = {
  records: TrendRecord[];
  loadedAt: string;
  since: string | null;
  truncatedStreams: string[];
  changedDuringLoad: boolean;
};

type Stream = { path: string; label: string; filters: Record<string, string>; cap: number };
type PageResult = { records: TrendRecord[]; truncated: boolean; changed: boolean };
type HistoryOptions = {
  signal: AbortSignal;
  token: string | null;
  onUnauthorized: () => void;
  now?: Date;
  fetcher?: typeof fetch;
};

// A BP comparison needs two independently bounded metric queries so a large
// systolic stream cannot displace all diastolic observations, or vice versa.
function streamsFor({ metric, crew }: TrendSelection): Stream[] {
  const crewFilter: Record<string, string> = crew === 'all' ? {} : { crew_id: crew };
  if (metric === 'blood_pressure') return [
    { path: 'telemetry', label: 'systolic blood pressure', filters: { ...crewFilter, metric: 'bp_systolic' }, cap: BP_STREAM_CAP },
    { path: 'telemetry', label: 'diastolic blood pressure', filters: { ...crewFilter, metric: 'bp_diastolic' }, cap: BP_STREAM_CAP },
  ];
  if (metric === 'dose') return [{ path: 'radiation', label: 'radiation', filters: crewFilter, cap: TREND_RECORD_CAP }];
  if (metric.startsWith('env:')) return [{ path: 'environment', label: 'environment', filters: { metric: metric.slice(4) }, cap: TREND_RECORD_CAP }];
  // Entity ID also covers radiation alerts associated with a crew member;
  // all-alerts intentionally includes crew, radiation and environment alerts.
  if (metric === 'alerts') return [{ path: 'alerts', label: 'alerts', filters: crew === 'all' ? {} : { entity_id: crew }, cap: TREND_RECORD_CAP }];
  return [{ path: 'telemetry', label: metric, filters: { ...crewFilter, metric }, cap: TREND_RECORD_CAP }];
}

async function fetchStream(stream: Stream, since: string | null, until: string, options: HistoryOptions): Promise<PageResult> {
  const records: TrendRecord[] = [];
  const seen = new Set<string>();
  let offset = 0, firstTotal: number | undefined, changed = false, hasMore = false;
  do {
    if (options.signal.aborted) throw new DOMException('History request cancelled.', 'AbortError');
    const params = new URLSearchParams({ ...stream.filters, limit: String(Math.min(TREND_PAGE_SIZE, stream.cap - offset)), offset: String(offset), until });
    if (since) params.set('since', since);
    const headers = new Headers();
    if (options.token) headers.set('Authorization', `Bearer ${options.token}`);
    let response: Response;
    try {
      response = await (options.fetcher ?? fetch)(`/api/${stream.path}?${params}`, { headers, signal: options.signal });
    } catch (error) {
      if (options.signal.aborted) throw error;
      throw new Error('Unable to reach the local service. Check the connection and try again.');
    }
    if (!response.ok) {
      if (response.status === 401 && !options.signal.aborted) options.onUnauthorized();
      let message = response.status === 403 ? 'Your role does not have permission to view this history.' : `History request failed (${response.status}).`;
      try { const body = await response.json(); if (body && typeof body.error === 'string') message = body.error; } catch { /* Keep the status-based message. */ }
      throw new Error(message);
    }
    const moreHeader = response.headers.get('X-Has-More');
    if (moreHeader !== 'true' && moreHeader !== 'false') throw new Error('History response is missing valid pagination information. Retry the request.');
    const totalHeader = response.headers.get('X-Total-Count');
    if (totalHeader !== null && /^\d+$/.test(totalHeader)) {
      const total = Number(totalHeader);
      if (firstTotal === undefined) firstTotal = total;
      else if (total !== firstTotal) changed = true;
    }
    const rows: unknown = await response.json();
    if (!Array.isArray(rows) || rows.length > Math.min(TREND_PAGE_SIZE, stream.cap - offset)) throw new Error('History returned an invalid page. Retry the request.');
    hasMore = moreHeader === 'true';
    if (hasMore && rows.length === 0) throw new Error('History pagination stopped unexpectedly. Retry the request.');
    for (const row of rows) {
      if (!row || typeof row.id !== 'string') throw new Error('History returned an invalid record. Retry the request.');
      if (seen.has(row.id)) changed = true;
      else { seen.add(row.id); records.push(row as TrendRecord); }
    }
    offset += rows.length;
  } while (hasMore && offset < stream.cap);
  return { records, truncated: hasMore, changed };
}

/** One fixed time window per load; each page is bounded, newest pages first. */
export async function loadTrendHistory(selection: TrendSelection, options: HistoryOptions): Promise<TrendHistory> {
  const end = selection.period === 'custom' ? new Date(selection.until || '') : options.now ?? new Date();
  const days = Number(selection.period);
  const start = selection.period === 'all' ? null : selection.period === 'custom' ? new Date(selection.since || '') : new Date(end.getTime() - days * 86_400_000);
  if (!Number.isFinite(end.getTime()) || (start && (!Number.isFinite(start.getTime()) || start.getTime() > end.getTime()))) throw new Error('Enter a valid start and end; start must not be after end.');
  const until = end.toISOString();
  const since = start?.toISOString() ?? null;
  const streams = streamsFor(selection);
  const pages = await Promise.all(streams.map(stream => fetchStream(stream, since, until, options)));
  return {
    records: pages.flatMap(page => page.records), loadedAt: until, since,
    truncatedStreams: streams.filter((_, i) => pages[i].truncated).map(stream => stream.label),
    changedDuringLoad: pages.some(page => page.changed),
  };
}
