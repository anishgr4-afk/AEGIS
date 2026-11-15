import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
}
const { loadTrendHistory, TREND_PAGE_SIZE, TREND_RECORD_CAP } = await load('../src/views/trendHistory.ts');
const { chartSegments, formatTrendValue, formatTrendAxis } = await load('../src/views/trendChartGaps.ts');
const end = new Date('2025-03-01T12:34:56.000Z');
const selection = { metric: 'spo2', crew: 'crew-1', period: '7' };
const options = fetcher => ({ signal: new AbortController().signal, token: 'secret', onUnauthorized: () => {}, now: end, fetcher });
const record = i => ({ id: `r-${i}`, timestamp: '2025-02-28T00:00:00Z', value: i, unit: '%' });
const url = path => new URL(path, 'http://localhost');
const page = (rows, more, total) => new Response(JSON.stringify(rows), { headers: { 'Content-Type': 'application/json', 'X-Has-More': String(more), 'X-Total-Count': String(total) } });

test('loads all bounded pages with fixed inclusive bounds, auth, and server-side filters', async () => {
  const calls = [], total = 1205;
  const result = await loadTrendHistory(selection, options(async (path, init) => {
    const u = url(path), offset = Number(u.searchParams.get('offset')), limit = Number(u.searchParams.get('limit'));
    calls.push(u); assert.equal(init.headers.get('Authorization'), 'Bearer secret');
    const rows = Array.from({ length: Math.min(limit, total - offset) }, (_, i) => record(offset + i)).reverse();
    return page(rows, offset + rows.length < total, total);
  }));
  assert.equal(result.records.length, total);
  assert.deepEqual(calls.map(u => Number(u.searchParams.get('offset'))), [0, 500, 1000]);
  for (const u of calls) {
    assert.equal(u.pathname, '/api/telemetry'); assert.equal(u.searchParams.get('limit'), String(TREND_PAGE_SIZE));
    assert.equal(u.searchParams.get('crew_id'), 'crew-1'); assert.equal(u.searchParams.get('metric'), 'spo2');
    assert.equal(u.searchParams.get('since'), '2025-02-22T12:34:56.000Z'); assert.equal(u.searchParams.get('until'), end.toISOString());
  }
  assert.deepEqual(result.truncatedStreams, []); assert.equal(result.changedDuringLoad, false);
});

test('uses an explicit older interval and rejects reversed or incomplete bounds', async () => {
  const custom = { ...selection, period: 'custom', since: '2020-01-01T00:00:00.000Z', until: '2020-02-01T00:00:00.000Z' };
  const result = await loadTrendHistory(custom, options(async path => {
    const u = url(path); assert.equal(u.searchParams.get('since'), custom.since); assert.equal(u.searchParams.get('until'), custom.until);
    return page([], false, 0);
  }));
  assert.equal(result.since, custom.since); assert.equal(result.loadedAt, custom.until);
  await assert.rejects(loadTrendHistory({ ...custom, since: custom.until, until: custom.since }, options(async () => { throw Error('must not fetch'); })), /valid start and end/);
  await assert.rejects(loadTrendHistory({ ...custom, since: '' }, options(async () => { throw Error('must not fetch'); })), /valid start and end/);
});

test('caps newest records and reports more pages honestly', async () => {
  let calls = 0;
  const result = await loadTrendHistory({ metric: 'sleep', crew: 'all', period: 'all' }, options(async path => {
    const u = url(path), offset = Number(u.searchParams.get('offset')), limit = Number(u.searchParams.get('limit'));
    calls++; assert.equal(u.searchParams.get('since'), null); assert.equal(u.searchParams.get('crew_id'), null);
    return page(Array.from({ length: limit }, (_, i) => record(offset + i)), true, TREND_RECORD_CAP + 500);
  }));
  assert.equal(calls, TREND_RECORD_CAP / TREND_PAGE_SIZE); assert.equal(result.records.length, TREND_RECORD_CAP);
  assert.deepEqual(result.truncatedStreams, ['sleep']);
});

test('blood pressure uses independent capped systolic and diastolic queries', async () => {
  const calls = [];
  const result = await loadTrendHistory({ metric: 'blood_pressure', crew: 'c', period: '30' }, options(async path => {
    const u = url(path), metric = u.searchParams.get('metric'), offset = Number(u.searchParams.get('offset')), limit = Number(u.searchParams.get('limit'));
    calls.push(metric); assert.equal(u.searchParams.get('crew_id'), 'c');
    const total = metric === 'bp_systolic' ? 2400 : 3;
    return page(Array.from({ length: Math.min(limit, total - offset) }, (_, i) => ({ ...record(offset + i), id: `${metric}-${offset + i}` })), offset + limit < total, total);
  }));
  assert.equal(calls.filter(c => c === 'bp_systolic').length, 4); assert.equal(calls.filter(c => c === 'bp_diastolic').length, 1);
  assert.equal(result.records.length, TREND_RECORD_CAP / 2 + 3); assert.deepEqual(result.truncatedStreams, ['systolic blood pressure']);
});

test('maps radiation, environmental and alert scopes to supported endpoint filters', async () => {
  const scenarios = [
    [{ metric: 'dose', crew: 'mission', period: '1' }, '/api/radiation', { crew_id: 'mission' }, ['metric', 'entity_id']],
    [{ metric: 'env:co2', crew: 'crew-1', period: '90' }, '/api/environment', { metric: 'co2' }, ['crew_id', 'entity_id']],
    [{ metric: 'alerts', crew: 'crew-1', period: 'all' }, '/api/alerts', { entity_id: 'crew-1' }, ['crew_id', 'metric', 'entity_type']],
    [{ metric: 'alerts', crew: 'all', period: 'all' }, '/api/alerts', {}, ['crew_id', 'entity_id', 'entity_type']],
  ];
  for (const [selected, expectedPath, filters, absent] of scenarios) {
    await loadTrendHistory(selected, options(async path => {
      const u = url(path); assert.equal(u.pathname, expectedPath);
      for (const [key, value] of Object.entries(filters)) assert.equal(u.searchParams.get(key), value);
      for (const key of absent) assert.equal(u.searchParams.has(key), false);
      return page([], false, 0);
    }));
  }
});

test('detects changing totals and de-duplicates overlapping pages', async () => {
  const result = await loadTrendHistory(selection, options(async path => {
    const offset = Number(url(path).searchParams.get('offset'));
    return offset === 0 ? page(Array.from({ length: 500 }, (_, i) => record(i)), true, 501) : page([record(499), record(500)], false, 502);
  }));
  assert.equal(result.records.length, 501); assert.equal(result.changedDuringLoad, true);
});

test('refuses unsafe pagination metadata and empty continuing pages', async () => {
  await assert.rejects(loadTrendHistory(selection, options(async () => new Response('[]'))), /pagination information/i);
  await assert.rejects(loadTrendHistory(selection, options(async () => page([], true, 1))), /pagination stopped/i);
});

test('aborts in-flight work without requesting another page', async () => {
  const controller = new AbortController(); let calls = 0;
  const pending = loadTrendHistory(selection, { ...options(async (_path, init) => {
    calls++;
    return new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }));
  }), signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' }); assert.equal(calls, 1);
});

test('dispatches unauthorized handling and surfaces service errors', async () => {
  let expired = 0;
  await assert.rejects(loadTrendHistory(selection, { ...options(async () => new Response('{"error":"session expired"}', { status: 401 })), onUnauthorized: () => { expired++; } }), /session expired/);
  assert.equal(expired, 1);
  await assert.rejects(loadTrendHistory(selection, options(async () => { throw new Error('offline'); })), /Unable to reach/);
});

test('breaks lines across observed gaps, including gaps hidden by sampling', () => {
  const point = day => ({ timestamp: `2025-01-${String(day).padStart(2, '0')}T00:00:00Z`, value: day, series: 'A', unit: '%' });
  const actual = [1, 2, 3, 20, 21].map(point);
  const samples = [actual[0], actual[2], actual[4]].map(p => ({ ...p, time: Date.parse(p.timestamp) }));
  assert.deepEqual(chartSegments(actual, samples, false).map(s => s.map(p => p.value)), [[1, 3], [21]]);
  const alerts = [point(1), point(3)].map(p => ({ ...p, time: Date.parse(p.timestamp) }));
  assert.equal(chartSegments(alerts, alerts, true).length, 2);
  assert.equal(chartSegments([], [], false).length, 0);
});

test('preserves small radiation values and distinguishable axis ticks', () => {
  assert.match(formatTrendValue(0.0000004), /4\.0+e-7/);
  assert.notEqual(formatTrendAxis(0.0000004, 0.0000001), formatTrendAxis(0.0000005, 0.0000001));
  assert.notEqual(formatTrendAxis(1000.000001, 0.000001), formatTrendAxis(1000.000002, 0.000001));
});
