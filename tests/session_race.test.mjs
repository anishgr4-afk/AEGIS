import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// Exercise the actual production modules and React event handlers without adding a DOM dependency.
const require = createRequire(import.meta.url);
function bundle(path) {
  return buildSync({
    entryPoints: [fileURLToPath(new URL(path, import.meta.url))],
    bundle: true, platform: 'node', format: 'cjs', write: false,
    external: ['react', 'react/jsx-runtime', 'lucide-react', 'three'], loader: { '.css': 'empty' },
  }).outputFiles[0].text;
}
const libBundle = bundle('../src/lib.ts');
const panelBundle = bundle('../src/views/SecurityPanel.tsx');
const appBundle = bundle('../src/App.tsx');
const opsBundle = bundle('../src/views/OperationsViews.tsx');
function load(code, mockReact) {
  const module = { exports: {} };
  new Function('require', 'module', 'exports', code)(
    name => name === 'react' && mockReact ? mockReact : require(name), module, module.exports,
  );
  return module.exports;
}

function hooks() {
  const state = [], refs = [], effects = [], writes = [];
  let cursor = 0, refCursor = 0;
  return {
    state, effects, writes,
    react: {
      ...React,
      useState(initial) {
        const index = cursor++;
        if (!(index in state)) state[index] = typeof initial === 'function' ? initial() : initial;
        return [state[index], value => {
          state[index] = typeof value === 'function' ? value(state[index]) : value;
          writes.push(index);
        }];
      },
      useRef(value) { const index = refCursor++; return refs[index] ??= { current: value }; },
      useCallback(fn) { return fn; },
      useEffect(effect) { effects.push(effect); },
      useMemo(fn) { return fn(); },
    },
    render(component, props) { cursor = 0; refCursor = 0; effects.length = 0; return component(props); },
  };
}
function find(element, predicate) {
  if (!element || typeof element !== 'object') return undefined;
  if (predicate(element)) return element;
  for (const child of React.Children.toArray(element.props?.children)) {
    const match = find(child, predicate);
    if (match) return match;
  }
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const jsonResponse = (status, value) => new Response(JSON.stringify(value), {
  status, headers: { 'content-type': 'application/json' },
});

function environment(t) {
  const saved = new Map(['sessionStorage', 'window', 'fetch', 'FormData'].map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const entries = new Map();
  const storage = {
    getItem: key => entries.has(key) ? entries.get(key) : null,
    setItem: (key, value) => entries.set(key, String(value)),
    removeItem: key => entries.delete(key),
    clear: () => entries.clear(),
  };
  const win = new EventTarget();
  const events = [];
  win.addEventListener('checkpoint:session-expired', event => events.push(event));
  Object.assign(globalThis, { sessionStorage: storage, window: win });
  t.after(() => {
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });
  return { storage, win, events };
}
function signIn(storage, token = 'session-a', expiry = '2030-01-01T00:00:00Z') {
  storage.setItem('checkpoint_token', token);
  storage.setItem('checkpoint_expiry', expiry);
}
function expectSession(storage, token, expiry = '2030-01-01T00:00:00Z') {
  assert.equal(storage.getItem('checkpoint_token'), token);
  assert.equal(storage.getItem('checkpoint_expiry'), expiry);
}

for (const kind of ['sign-in to another account', 'sign-out']) {
  test(`a stale 401 after ${kind} rejects the request but cannot expire a newer session`, async t => {
    const { storage, events } = environment(t);
    const { api } = load(libBundle);
    signIn(storage);
    const pending = deferred();
    let authorization;
    globalThis.fetch = (_url, init) => { authorization = init.headers.get('Authorization'); return pending.promise; };
    const request = api('/snapshot');
    assert.equal(authorization, 'Bearer session-a');
    storage.removeItem('checkpoint_token'); storage.removeItem('checkpoint_expiry');
    if (kind === 'sign-in to another account') signIn(storage, 'session-b', '2031-02-01T00:00:00Z');
    pending.resolve(jsonResponse(401, { error: 'Old session rejected' }));
    await assert.rejects(request, /Old session rejected/);
    if (kind === 'sign-in to another account') expectSession(storage, 'session-b', '2031-02-01T00:00:00Z');
    else expectSession(storage, null, null);
    assert.equal(events.length, 0);
  });
}

test('a genuine 401 clears both session fields and dispatches exactly one expiry', async t => {
  const { storage, events } = environment(t);
  const { api } = load(libBundle);
  signIn(storage);
  globalThis.fetch = async () => jsonResponse(401, { error: 'Expired' });
  await assert.rejects(api('/snapshot'), /Expired/);
  expectSession(storage, null, null);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'checkpoint:session-expired');
});

test('a malformed 401 response still expires the current session', async t => {
  const { storage, events } = environment(t);
  const { api } = load(libBundle);
  signIn(storage);
  globalThis.fetch = async () => new Response('not json', { status: 401, headers: { 'content-type': 'application/json' } });
  await assert.rejects(api('/snapshot'));
  expectSession(storage, null, null);
  assert.equal(events.length, 1);
});

test('overlapping same-session 401s only expire once', async t => {
  const { storage, events } = environment(t);
  const { api } = load(libBundle);
  signIn(storage);
  const responses = [deferred(), deferred()];
  let next = 0;
  globalThis.fetch = () => responses[next++].promise;
  const a = api('/snapshot'), b = api('/auth/security');
  // Both fetch calls have already captured the same token.
  for (const pending of responses) pending.resolve(jsonResponse(401, { error: 'Expired' }));
  await Promise.allSettled([a, b]);
  assert.equal(events.length, 1);
  expectSession(storage, null, null);
});

test('login 401 and a 401 from an unauthenticated request never expire a later sign-in', async t => {
  const { storage, events } = environment(t);
  const { api } = load(libBundle);
  const pending = deferred();
  globalThis.fetch = () => pending.promise;
  const request = api('/snapshot');
  signIn(storage, 'session-b');
  pending.resolve(jsonResponse(401, { error: 'No token' }));
  await assert.rejects(request, /No token/);
  globalThis.fetch = async () => jsonResponse(401, { error: 'Bad credentials' });
  await assert.rejects(api('/auth/login'), /Bad credentials/);
  expectSession(storage, 'session-b');
  assert.equal(events.length, 0);
});

function setupPanel(t) {
  const env = environment(t);
  const h = hooks();
  const { SecurityPanel } = load(panelBundle, h.react);
  const render = () => h.render(SecurityPanel);
  let statusRequests = 0;
  const password = deferred();
  let passwordHeaders;
  globalThis.fetch = (url, init) => {
    if (url.endsWith('/auth/security')) {
      statusRequests++;
      return Promise.resolve(jsonResponse(200, { demo_password_in_use: false, min_password_length: 12, max_password_length: 256 }));
    }
    assert.ok(url.endsWith('/auth/password'));
    passwordHeaders = init.headers;
    return password.promise;
  };
  globalThis.FormData = class { constructor(form) { this.fields = form.fields; } get(key) { return this.fields[key] ?? null; } };
  signIn(env.storage);
  let tree = render();
  const cleanups = h.effects.map(effect => effect()).filter(value => typeof value === 'function');
  const form = {
    fields: { current_password: 'old passphrase', new_password: 'a new passphrase', confirm_password: 'a new passphrase' },
    resetCount: 0, reset() { this.resetCount++; },
  };
  return {
    ...env, h, render, password, form,
    statusRequests: () => statusRequests,
    passwordHeaders: () => passwordHeaders,
    submit() {
      tree = render();
      return find(tree, node => node.type === 'form').props.onSubmit({ preventDefault() {}, currentTarget: form });
    },
    unmount() { for (const cleanup of cleanups) cleanup(); },
  };
}

test('stale successful password change preserves a newer session and reports the previous account', async t => {
  const panel = setupPanel(t);
  await tick();
  const request = panel.submit();
  assert.equal(panel.passwordHeaders().get('Authorization'), 'Bearer session-a');
  signIn(panel.storage, 'session-b', '2031-02-01T00:00:00Z');
  panel.password.resolve(jsonResponse(200, { success: true }));
  await request;
  expectSession(panel.storage, 'session-b', '2031-02-01T00:00:00Z');
  assert.equal(panel.events.length, 0);
  assert.equal(panel.form.resetCount, 0);
  const notice = find(panel.render(), node => node.props?.role === 'status' && typeof node.props.children === 'string' && node.props.children.includes('previous account'));
  assert.match(notice.props.children, /completed/i);
  assert.match(notice.props.children, /current local session was not cleared/i);
});

test('same-session successful password change clears the session with a specific notice', async t => {
  const panel = setupPanel(t);
  await tick();
  const request = panel.submit();
  panel.password.resolve(jsonResponse(200, { success: true }));
  await request;
  expectSession(panel.storage, null, null);
  assert.equal(panel.events.length, 1);
  assert.equal(panel.events[0].detail.reason, 'password-changed');
  assert.match(panel.events[0].detail.message, /password changed/i);
  assert.equal(panel.form.resetCount, 1);
});

test('a stale 401 from password change does not expire the new account', async t => {
  const panel = setupPanel(t);
  await tick();
  const request = panel.submit();
  signIn(panel.storage, 'session-b');
  panel.password.resolve(jsonResponse(401, { error: 'Old session expired' }));
  await request;
  expectSession(panel.storage, 'session-b');
  assert.equal(panel.events.length, 0);
  assert.equal(panel.form.resetCount, 0);
});

test('unmounted password panel does not update its state for a stale completion', async t => {
  const panel = setupPanel(t);
  await tick();
  const request = panel.submit();
  panel.unmount();
  panel.h.writes.length = 0;
  signIn(panel.storage, 'session-b');
  panel.password.resolve(jsonResponse(200, { success: true }));
  await request;
  expectSession(panel.storage, 'session-b');
  assert.deepEqual(panel.h.writes, []);
  assert.equal(panel.events.length, 0);
});

test('security status refresh keeps using the current session normally', async t => {
  const panel = setupPanel(t);
  await tick();
  assert.equal(panel.statusRequests(), 1);
  const refresh = find(panel.render(), node => node.props?.['aria-label'] === 'Refresh security status');
  assert.equal(refresh.props.disabled, false);
  refresh.props.onClick();
  await tick();
  assert.equal(panel.statusRequests(), 2);
  assert.equal(panel.h.state[0].demo_password_in_use, false);
  assert.equal(panel.h.state[1], false);
  assert.equal(panel.events.length, 0);
});

test('a stale session-restore 401 does not remove the new token or unset the new user', async t => {
  const { storage, events } = environment(t);
  const h = hooks();
  const App = load(appBundle, h.react).default;
  signIn(storage);
  const pending = deferred();
  globalThis.fetch = () => pending.promise;
  h.render(App);
  const cleanup = h.effects[0]();
  const newUser = { id: 'b', username: 'new-user', role: 'Commander', display_name: 'New User' };
  signIn(storage, 'session-b');
  h.state[0] = newUser;
  pending.resolve(jsonResponse(401, { error: 'Old session' }));
  await tick();
  expectSession(storage, 'session-b');
  assert.equal(h.state[0], newUser);
  assert.equal(events.length, 0);
  cleanup();
});

test('a stale successful session restore cannot replace the new user', async t => {
  const { storage, events } = environment(t);
  const h = hooks();
  const App = load(appBundle, h.react).default;
  signIn(storage);
  const pending = deferred();
  globalThis.fetch = () => pending.promise;
  h.render(App);
  const cleanup = h.effects[0]();
  const newUser = { id: 'b', username: 'new-user', role: 'Commander', display_name: 'New User' };
  signIn(storage, 'session-b');
  h.state[0] = newUser;
  pending.resolve(jsonResponse(200, { id: 'a', username: 'old-user' }));
  await tick();
  expectSession(storage, 'session-b');
  assert.equal(h.state[0], newUser);
  assert.equal(events.length, 0);
  cleanup();
});

test('a delayed expiry timer is scoped to its original token', t => {
  const { storage, win, events } = environment(t);
  const h = hooks();
  const App = load(appBundle, h.react).default;
  let timer;
  win.setTimeout = fn => { timer = fn; return 1; };
  win.clearTimeout = () => {};
  signIn(storage, 'session-a', new Date(Date.now() + 60_000).toISOString());
  h.state[0] = { id: 'a', username: 'old', role: 'Commander', display_name: 'Old' };
  h.render(App);
  h.effects[1]();
  assert.equal(typeof timer, 'function');
  signIn(storage, 'session-b');
  timer();
  expectSession(storage, 'session-b');
  assert.equal(events.length, 0);
});

test('an expired current-session timer clears the session normally', t => {
  const { storage, events } = environment(t);
  const h = hooks();
  const App = load(appBundle, h.react).default;
  signIn(storage, 'session-a', new Date(Date.now() - 1000).toISOString());
  h.state[0] = { id: 'a', username: 'old', role: 'Commander', display_name: 'Old' };
  h.render(App);
  h.effects[1]();
  expectSession(storage, null, null);
  assert.equal(events.length, 1);
});

test('a stale binary-export 401 cannot expire a newer sign-in', async t => {
  const { storage, events } = environment(t);
  const h = hooks();
  const { LogView } = load(opsBundle, h.react);
  signIn(storage);
  const pending = deferred();
  globalThis.fetch = () => pending.promise;
  const tree = h.render(LogView, { user: { role: 'Commander' } });
  const heading = find(tree, node => node.props?.title === 'Mission log');
  const button = find(heading.props.actions, node => node.props?.children === 'JSON');
  button.props.onClick();
  signIn(storage, 'session-b');
  pending.resolve(jsonResponse(401, { error: 'Old export' }));
  await tick();
  expectSession(storage, 'session-b');
  assert.equal(events.length, 0);
});

test('security and administrator forms disclose that the denylist is limited', () => {
  const { SecurityPanel } = load(panelBundle);
  const { SettingsView } = load(opsBundle);
  const security = renderToStaticMarkup(React.createElement(SecurityPanel));
  const settings = renderToStaticMarkup(React.createElement(SettingsView, {
    user: { id: 'admin', username: 'admin', role: 'Administrator', display_name: 'Admin' },
    data: { settings: { thresholds: {} } }, refresh: async () => {},
  }));
  for (const markup of [security, settings]) {
    assert.match(markup, /limited set of common values/i);
    assert.match(markup, /not a comprehensive check for common or compromised passwords/i);
  }
});
