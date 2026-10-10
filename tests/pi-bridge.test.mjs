import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { BridgeClient, fillDraft } from '../adapters/pi/lib/bridge-client.ts';

test('draft fill never overwrites, submits or runs while model is active', () => {
  let text = '', idle = true;
  const ctx = { mode: 'tui', isIdle: () => idle, ui: { getEditorText: () => text, setEditorText: v => { text = v; } } };
  assert.equal(fillDraft(ctx, 'first'), 'filled');
  assert.equal(text, 'first');
  assert.equal(fillDraft(ctx, 'second'), 'occupied');
  assert.equal(text, 'first');
  text = ''; idle = false;
  assert.equal(fillDraft(ctx, 'third'), 'busy');
  assert.equal(text, '');
  assert.equal(fillDraft({ ...ctx, mode: 'rpc' }, 'fourth'), 'unsupported');
});

test('real bridge process serves scoped tools and shuts down with its parent pipe', async () => {
  const folder = await mkdtemp(path.join(tmpdir(), 'review-pi-'));
  const client = new BridgeClient();
  try {
    await client.start(folder, { ...process.env, REVIEW_COMPANION_DATA_DIR: path.join(folder, 'state') });
    await client.switchSession('a');
    const result = await client.call('open_review', {});
    assert.equal(result.structuredContent.empty, true);
    const specs = await client.request('/agent/specs');
    assert.equal(specs.length, 11);
    assert.ok(specs.some(tool => tool.name === "set_file_risks"));
    assert.ok(!specs.some(s => ['open_editor','open_project'].includes(s.name)));
    const old = client.session;
    await client.switchSession('b');
    await assert.rejects(client.request('/agent/poll', { session: old }), /会话已变化/);
    const exited = new Promise(resolve => client.child.once('exit', resolve));
    client.stop(); await exited;
    assert.equal(client.connection, null);
  } finally { client.stop(); await rm(folder, { recursive: true, force: true }); }
});
