import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { BridgeClient, dataDirectory, root } from '../adapters/pi/lib/bridge-client.ts';
import { writeConfig } from '../adapters/pi/lib/config.ts';

test('adapter resolves shared package root and preserves explicit Pi data directory', () => {
  assert.equal(root, path.resolve(import.meta.dirname, '..') + path.sep);
  assert.ok(fs.existsSync(path.join(root, 'backend/tool-specs.json')));
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-paths-'));
  try {
    const env = { PI_CODING_AGENT_DIR: folder };
    assert.equal(dataDirectory(folder, env), undefined);
    writeConfig({ data_dir: path.join(folder, 'prior-records') }, env);
    assert.equal(dataDirectory(folder, env), path.join(folder, 'prior-records'));
    assert.equal(dataDirectory(folder, { ...env, REVIEW_COMPANION_DATA_DIR: path.join(folder, 'override') }), path.join(folder, 'override'));
  } finally { fs.rmSync(folder, { recursive: true, force: true }); }
});

test('bridge forwards runtime configuration and omits model credentials and implicit data directory', async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pi-child-'));
  const executable = path.join(folder, 'python-fixture');
  fs.writeFileSync(executable, `#!${process.execPath}\nconsole.log(JSON.stringify({baseUrl:'http://fixture.invalid',agentToken:'synthetic',browserToken:'synthetic',args:process.argv.slice(2),env:process.env}));process.stdin.resume();process.stdin.on('end',()=>process.exit(0));\n`, { mode: 0o700 });
  const client = new BridgeClient();
  try {
    await client.start(folder, { ...process.env, PI_CODING_AGENT_DIR: folder, REVIEW_COMPANION_DATA_DIR: '', REVIEW_COMPANION_PYTHON: executable, REVIEW_COMPANION_CONFIG: '/synthetic/runtime.json', REVIEW_COMPANION_GIT: '/synthetic/git', REVIEW_COMPANION_EDITOR: '/synthetic/editor', MENSHEN_API_KEY: 'never-forward' });
    assert.deepEqual(client.connection.args.slice(-2), ['--host', 'pi']);
    assert.ok(!client.connection.args.includes('--data-dir'));
    assert.equal(client.connection.env.REVIEW_COMPANION_CONFIG, '/synthetic/runtime.json');
    assert.equal(client.connection.env.REVIEW_COMPANION_GIT, '/synthetic/git');
    assert.equal(client.connection.env.REVIEW_COMPANION_EDITOR, '/synthetic/editor');
    assert.equal(client.connection.env.MENSHEN_API_KEY, undefined);
  } finally {
    const exited = new Promise(resolve => client.child.once('exit', resolve));
    client.stop(); await exited;
    fs.rmSync(folder, { recursive: true, force: true });
  }
});
