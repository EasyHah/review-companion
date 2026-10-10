import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { resolveCredential, writeConfig } from '../adapters/pi/lib/config.ts';

async function fixture(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(),'review-config-'));
  const env = { PI_CODING_AGENT_DIR: dir };
  const write = (file, data) => fs.writeFileSync(path.join(dir,file), JSON.stringify(data));
  try { await run({ dir, env, write }); } finally { fs.rmSync(dir,{recursive:true,force:true}); }
}
test('missing credentials allow the plugin to load without reading machine-specific files', () => fixture(async ({env}) => {
  assert.equal(await resolveCredential({env}), undefined);
}));
test('explicit environment key wins, then Pi login credential', () => fixture(async ({env}) => {
  assert.equal((await resolveCredential({env:{...env,MENSHEN_API_KEY:'env-key'},credential:{key:'login-key'}})).key,'env-key');
  assert.equal((await resolveCredential({env,credential:{key:'login-key'}})).key,'login-key');
}));
test('does not probe unrelated user providers or proxy credentials', () => fixture(async ({env,write}) => {
  write('models.json',{providers:{proxy:{baseUrl:'http://127.0.0.1:1111',apiKey:'private-proxy-key'}}});
  write('auth.json',{proxy:{type:'api_key',key:'private-proxy-key'}});
  assert.equal(await resolveCredential({env}),undefined);
}));
test('per-machine file reference is explicit, exports no key into plugin config', () => fixture(async ({env,dir,write}) => {
  write('private.json',{LOCAL_TOKEN:'synthetic-key'});
  writeConfig({credential:{type:'file',value:path.join(dir,'private.json'),field:'LOCAL_TOKEN'}},env);
  assert.equal((await resolveCredential({env})).key,'synthetic-key');
  assert.ok(!fs.readFileSync(path.join(dir,'review-companion.json'),'utf8').includes('synthetic-key'));
}));
test('broken config errors never include sensitive file contents', () => fixture(async ({env,dir}) => {
  fs.writeFileSync(path.join(dir,'auth.json'),'{SECRET-MARKER');
  await assert.rejects(resolveCredential({env}),error=>!error.message.includes('SECRET-MARKER'));
}));
