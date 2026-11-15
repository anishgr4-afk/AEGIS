import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// Load the production TypeScript modules without introducing a test dependency.
async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
}
const { startSnapshotPolling } = await load('../src/views/snapshotPolling.ts');
const { prepareChart } = await load('../src/views/chartData.ts');
const { limitedWindows, hasOmittedActiveAlerts } = await load('../src/views/snapshotWindow.ts');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate, timeout = 1000) {
  const start = Date.now();
  while (!predicate()) {
    if (Date.now() - start > timeout) throw new Error('Timed out waiting for test condition');
    await sleep(2);
  }
}

test('manual requests coalesce behind a running poll and always follow writes', async () => {
  const requests = [], received = [], errors = [];
  let active = 0, maxActive = 0;
  const poller = startSnapshotPolling({
    intervalMs: 5000, timeoutMs: 2000, isVisible: () => true,
    request: signal => new Promise((resolve, reject) => {
      active++; maxActive = Math.max(active, maxActive);
      requests.push({ resolve: value => { active--; resolve(value); }, reject });
      signal.addEventListener('abort', () => { active--; reject(new Error('aborted')); }, { once: true });
    }),
    onData: value => received.push(value), onError: error => errors.push(error.message), onManualLoading: () => {},
  });
  await until(() => requests.length === 1);
  const a = poller.refresh(), b = poller.refresh();
  assert.equal(a, b, 'simultaneous refreshes coalesce');
  requests[0].resolve('old');
  await until(() => requests.length === 2);
  // A later write during that trailing request still requires another fresh read.
  const c = poller.refresh();
  requests[1].resolve('after-first-write');
  await until(() => requests.length === 3);
  requests[2].resolve('after-second-write');
  await Promise.all([a, b, c]);
  poller.stop();
  assert.deepEqual(received, ['old', 'after-first-write', 'after-second-write']);
  assert.equal(maxActive, 1);
  assert.deepEqual(errors, []);
});

test('hidden tabs pause automatic requests and visible tabs refresh immediately', async () => {
  let visible = true, count = 0;
  const poller = startSnapshotPolling({
    intervalMs: 30, timeoutMs: 1000, isVisible: () => visible,
    request: async () => { count++; return count; }, onData: () => {}, onError: () => {}, onManualLoading: () => {},
  });
  await until(() => count === 1);
  visible = false; poller.visibilityChanged();
  await sleep(120);
  assert.equal(count, 1);
  visible = true; poller.visibilityChanged();
  await until(() => count === 2);
  poller.stop();
  await sleep(80);
  assert.equal(count, 2);
});

test('a queued resume is discarded if the tab becomes hidden again', async () => {
  let visible = true, count = 0, finish;
  const poller = startSnapshotPolling({
    intervalMs: 50, timeoutMs: 1000, isVisible: () => visible,
    request: () => { count++; return new Promise(resolve => { finish = resolve; }); },
    onData: () => {}, onError: () => {}, onManualLoading: () => {},
  });
  await until(() => count === 1);
  visible = false; poller.visibilityChanged();
  visible = true; poller.visibilityChanged();
  visible = false; poller.visibilityChanged();
  finish('old');
  await sleep(100);
  assert.equal(count, 1);
  visible = true; poller.visibilityChanged();
  await until(() => count === 2);
  finish('new');
  poller.stop();
});

test('timeout aborts a stalled request and reports an error without overlapping', async () => {
  let calls = 0, active = 0, maxActive = 0, failure = '';
  const poller = startSnapshotPolling({
    intervalMs: 1000, timeoutMs: 25, isVisible: () => true,
    request: signal => new Promise((_resolve, reject) => {
      calls++; active++; maxActive = Math.max(maxActive, active);
      signal.addEventListener('abort', () => { active--; reject(new Error('aborted')); }, { once: true });
    }),
    onData: () => {}, onError: error => { failure = error.message; }, onManualLoading: () => {},
  });
  await until(() => !!failure);
  poller.stop();
  assert.match(failure, /timed out/i);
  assert.equal(calls, 1);
  assert.equal(maxActive, 1);
  assert.equal(active, 0);
});

test('large charts are bounded and preserve actual extremes and endpoints', () => {
  const count = 200000, epoch = Date.parse('2024-01-01T00:00:00Z');
  const points = Array.from({ length: count }, (_, i) => ({ timestamp: new Date(epoch + i * 1000).toISOString(), value: i === 4321 ? -777 : i === 123456 ? 999 : i % 80, series: 'A', unit: 'bpm' }));
  const chart = prepareChart(points);
  const samples = chart.plotted[0].samples;
  assert.equal(chart.validCount, count);
  assert.ok(chart.displayedCount <= 240);
  assert.equal(chart.ymin, -777); assert.equal(chart.ymax, 999);
  assert.ok(samples.some(p => p.value === -777)); assert.ok(samples.some(p => p.value === 999));
  assert.equal(samples[0].timestamp, points[0].timestamp); assert.equal(samples.at(-1).timestamp, points.at(-1).timestamp);
  assert.ok(samples.every(p => points.some(real => real.timestamp === p.timestamp && real.value === p.value)));
  assert.ok(samples.every((p, i) => i === 0 || p.time >= samples[i - 1].time));
});

test('many series and invalid records remain bounded and disclosed in metadata', () => {
  const points = Array.from({ length: 3000 }, (_, i) => ({ timestamp: '2024-01-01T00:00:00Z', value: i, series: `series-${i}`, unit: '%' }));
  points.push({ timestamp: 'invalid', value: 100, series: 'bad', unit: '%' });
  const chart = prepareChart(points);
  assert.equal(chart.validCount, 3000);
  assert.equal(chart.totalSeries, 3000);
  assert.equal(chart.plotted.length, 200);
  assert.ok(chart.displayedCount <= 1200);
});

test('history metadata is optional and identifies omitted active alerts', () => {
  const old = { alerts: [] };
  assert.deepEqual(limitedWindows(old), []);
  assert.equal(hasOmittedActiveAlerts(old), false);
  const snapshot = { alerts: [{ resolved: false }], history_window: { alerts: { returned: 1, total: 50, truncated: true, active: 2 }, measurements: { returned: 10, total: 10, truncated: false } } };
  assert.deepEqual(limitedWindows(snapshot), ['alerts 1/50']);
  assert.equal(hasOmittedActiveAlerts(snapshot), true);
});
