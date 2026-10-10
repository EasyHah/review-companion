import test from 'node:test';
import assert from 'node:assert/strict';
import { createBridgeRegistry } from '../adapters/pi/lib/bridge-registry.ts';

const ctx = (id, mode = 'rpc', cwd = '/tmp') => ({ mode, cwd, sessionManager: { getSessionId: () => id } });
function fixture() {
  const clients = [];
  const registry = createBridgeRegistry(() => {
    const client = { async start(cwd, env, options) { this.cwd = cwd; this.host = options.host; this.connection = {}; }, stop() { this.stopped = true; this.connection = null; } };
    clients.push(client); return client;
  });
  return { registry, clients };
}

test('web sessions have independent bridges even in the same project', async () => {
  const { registry } = fixture(), a = ctx('a'), b = ctx('b');
  const [one, two] = await Promise.all([registry.ensure(a), registry.ensure(b)]);
  assert.notEqual(one, two); assert.equal(one.host, 'pi_web');
  registry.release(a, 'quit'); assert.equal(one.stopped, true);
  assert.equal((await registry.ensure(b)), two); assert.ok(two.connection);
});

test('web reload preserves its own bridge; replacing a session closes only that session', async () => {
  const { registry, clients } = fixture(), a = ctx('a'), b = ctx('b');
  const one = await registry.ensure(a), two = await registry.ensure(b);
  registry.release(a, 'reload'); assert.equal(await registry.ensure(a), one);
  registry.release(a, 'new'); assert.equal(one.stopped, true);
  assert.equal(registry.peek(b), two); assert.equal(clients.length, 2);
  assert.notEqual(await registry.ensure(ctx('new')), one);
});

test('TUI replacement retains URL ownership and quit releases it', async () => {
  const { registry } = fixture(), a = ctx('a', 'tui'), b = ctx('b', 'tui');
  const one = await registry.ensure(a); assert.equal(one.host, 'pi');
  registry.release(a, 'new'); assert.equal(await registry.ensure(b), one);
  registry.release(b, 'quit'); assert.equal(one.stopped, true);
});

test('concurrent initialization is shared and shutdown during startup closes late process', async () => {
  let complete, starts = 0, stopped = 0;
  const ready = new Promise(resolve => { complete = resolve; });
  const registry = createBridgeRegistry(() => ({ start: async function () { starts++; await ready; this.connection = {}; }, stop() { stopped++; this.connection = null; } }));
  const a = ctx('a'), first = registry.ensure(a), second = registry.ensure(a);
  registry.release(a, 'quit'); complete();
  const results = await Promise.allSettled([first, second]);
  assert.ok(results.every(r => r.status === 'rejected')); assert.equal(starts, 1);
  assert.ok(stopped >= 1); assert.equal(registry.peek(a), undefined);
});
