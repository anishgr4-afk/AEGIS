import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// Bundle the production component in memory, without installing a DOM test package.
const require = createRequire(import.meta.url);
const bundle = buildSync({
  entryPoints: [fileURLToPath(new URL('../src/views/MissionBriefing.tsx', import.meta.url))],
  bundle: true, platform: 'node', format: 'cjs', write: false,
  external: ['react', 'react/jsx-runtime', 'lucide-react'], loader: { '.css': 'empty' },
}).outputFiles[0].text;
function load(reactOverride) {
  const module = { exports: {} };
  const localRequire = name => name === 'react' && reactOverride ? reactOverride : require(name);
  new Function('require', 'module', 'exports', bundle)(localRequire, module, module.exports);
  return module.exports;
}
const { MissionBriefing, getBriefingData, buildHandover } = load();

function snapshot(extra = {}) {
  return {
    crew: [{ id: 'c1', name: 'Fictional Crew Name', medical_history: 'private history' }],
    mission: { earth_connected: false, delay_minutes: 0.025, last_sync: '2025-01-02T03:04:00Z' },
    alerts: [
      { id: 'a1', resolved: false, acknowledged: false, severity: 'CRITICAL' },
      { id: 'a2', resolved: false, acknowledged: true, severity: 'CRITICAL' },
      { id: 'a3', resolved: true, acknowledged: false, severity: 'CRITICAL' },
    ],
    events: [
      { id: 'e1', category: 'operations', detail: 'Private free-text detail', timestamp: '2025-01-01T00:00:00Z' },
      { id: 'e2', category: 'medical', detail: 'Private medical detail', timestamp: '2025-01-03T00:00:00Z' },
      { id: 'e3', category: 'communication', detail: 'Message delivered', timestamp: '2025-01-02T00:00:00Z' },
    ],
    measurements: [], radiation: [], environment: [], settings: {}, ...extra,
  };
}

test('authoritative counts are used and omitted active alerts are disclosed', () => {
  const data = snapshot({ history_window: { alerts: { truncated: true, active: 8, active_critical: 6, unacknowledged_critical: 4 }, events: { truncated: true } } });
  const briefing = getBriefingData(data);
  assert.equal(briefing.unacknowledged, 4);
  assert.equal(briefing.countIsTotal, true);
  assert.match(briefing.coverage, /omits active alerts/i);
  assert.equal(briefing.link, 'Disconnected');
  assert.equal(briefing.delayText, '0.025 min one way');
  const markup = renderToStaticMarkup(React.createElement(MissionBriefing, { data }));
  assert.match(markup, /omits active alerts/i);
  assert.match(markup, /Private medical detail/); // Source events remain visible, but never enter handover.
  assert.match(markup, /Event history is limited/);
});

test('without full coverage or a trusted total the number is visibly scoped', () => {
  const briefing = getBriefingData(snapshot());
  assert.equal(briefing.unacknowledged, 1);
  assert.equal(briefing.countIsTotal, false);
  assert.match(briefing.coverage, /coverage is limited or unavailable/i);
  assert.match(buildHandover(snapshot()), /Unacknowledged critical alerts \(visible snapshot only\): 1/);
});

test('complete active window can establish a count even when resolved records are truncated', () => {
  const briefing = getBriefingData(snapshot({ history_window: { alerts: { truncated: true, active: 2 } } }));
  assert.equal(briefing.unacknowledged, 1);
  assert.equal(briefing.countIsTotal, true);
  assert.equal(briefing.coverage, '');
});

test('contradictory totals are not treated as reliable', () => {
  const briefing = getBriefingData(snapshot({ history_window: { alerts: { truncated: false, active: 0, unacknowledged_critical: 0 } } }));
  assert.equal(briefing.unacknowledged, 1);
  assert.equal(briefing.countIsTotal, false);
  assert.match(briefing.coverage, /inconsistent/i);
});

test('handover includes operational categories only, excluding free text and clinical content', () => {
  const data = snapshot({ history_window: { alerts: { truncated: false }, events: { truncated: true } } });
  const text = buildHandover(data, new Date('2025-01-04T00:00:00Z'));
  assert.match(text, /SIMULATED MISSION HANDOVER/);
  assert.match(text, /Crew profiles in snapshot: 1/);
  assert.match(text, /Communication/);
  assert.match(text, /Operations/);
  assert.ok(text.indexOf('Communication') < text.indexOf('Operations'));
  assert.match(text, /Event history is limited/);
  assert.doesNotMatch(text, /Private free-text detail|Private medical detail|private history|Fictional Crew Name/);
  assert.doesNotMatch(text, /- Medical/);
});

function find(element, predicate) {
  if (!element || typeof element !== 'object') return undefined;
  if (predicate(element)) return element;
  const children = React.Children.toArray(element.props?.children);
  for (const child of children) {
    const found = find(child, predicate);
    if (found) return found;
  }
  return undefined;
}

test('clipboard denial selects the generated text and announces a manual-copy fallback', async () => {
  const state = [];
  let cursor = 0, focused = false, selected = false;
  const mockReact = {
    useState(initial) {
      const index = cursor++;
      if (!(index in state)) state[index] = initial;
      return [state[index], value => { state[index] = value; }];
    },
    useRef() { return { current: { focus() { focused = true; }, select() { selected = true; } } }; },
  };
  const { MissionBriefing: MockBriefing } = load(mockReact);
  const render = () => { cursor = 0; return MockBriefing({ data: snapshot() }); };
  const original = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async () => { throw new Error('denied'); } } } });
  try {
    let tree = render();
    find(tree, node => node.type === 'button' && node.props.onClick && React.Children.toArray(node.props.children).includes('Generate handover')).props.onClick();
    tree = render();
    find(tree, node => node.type === 'button' && React.Children.toArray(node.props.children).some(child => typeof child === 'string' && child.includes('Copy text'))).props.onClick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(focused, true);
    assert.equal(selected, true);
    tree = render();
    const status = find(tree, node => node.props?.role === 'status');
    assert.match(status.props.children, /Could not copy automatically/);
  } finally {
    if (original) Object.defineProperty(globalThis, 'navigator', original);
    else delete globalThis.navigator;
  }
});
