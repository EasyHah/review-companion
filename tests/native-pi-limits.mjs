// Uses the installed npm Pi's real catalog and compaction implementation. No network.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { menshenProvider } from '../adapters/pi/lib/menshen.ts';
import { createRouter } from '../adapters/pi/lib/router.ts';

const executable = process.env.PATH.split(path.delimiter).map(dir => path.join(dir,'pi')).find(file => fs.existsSync(file));
assert.ok(executable,'Pi executable is required for this integration check');
let pi = path.dirname(fs.realpathSync(executable));
while (true) {
  const manifest = path.join(pi,'package.json');
  if (fs.existsSync(manifest) && JSON.parse(fs.readFileSync(manifest,'utf8')).name === '@earendil-works/pi-coding-agent') break;
  const parent = path.dirname(pi);
  assert.notEqual(parent,pi,'This check requires an npm Pi installation');
  pi = parent;
}
const { getBuiltinModel } = await import(pathToFileURL(path.join(pi,'node_modules/@earendil-works/pi-ai/dist/providers/all.js')));
const { buildBaseOptions } = await import(pathToFileURL(path.join(pi,'node_modules/@earendil-works/pi-ai/dist/api/simple-options.js')));
const { compact, generateSummaryWithUsage } = await import(pathToFileURL(path.join(pi,'dist/core/compaction/compaction.js')));
const preset = menshenProvider({}, getBuiltinModel);
for (const [id, source] of [['deepseek-v4.1-flash','opencode'],['glm-5.3','zai']]) {
  const found = preset.models.find(m => m.id === id), builtin = getBuiltinModel(source,id);
  assert.equal(found.contextWindow,builtin.contextWindow);
  assert.equal(found.maxTokens,builtin.maxTokens);
}
const router = createRouter(preset.models);
const longContext = { messages:[{role:'user',content:[{type:'text',text:'synthetic '.repeat(22000)}],timestamp:Date.now()}] };
const physical = preset.models.find(model => model.id === 'glm-5.3');
assert.equal(buildBaseOptions({...physical,contextWindow:32768,maxTokens:4096},longContext,{},'synthetic').maxTokens,1);
assert.equal(buildBaseOptions(physical,longContext,{},'synthetic').maxTokens,physical.maxTokens);
const seen = [];
const usage = { input:0, output:0, cacheRead:0, cacheWrite:0, totalTokens:0, cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0} };
const stream = (model, context, options) => ({ result: async () => {
  seen.push(options.maxTokens);
  return { role:'assistant',content:[{type:'text',text:'Synthetic checkpoint'}],usage,
    stopReason: options.maxTokens <= 4096 ? 'length' : 'stop',timestamp:Date.now() };
} });
const messages = [{ role:'user', content:'Synthetic conversation to summarize', timestamp:Date.now() }];
const preparation = { firstKeptEntryId:'synthetic-id',messagesToSummarize:[],turnPrefixMessages:messages,
  isSplitTurn:true,tokensBefore:30000,fileOps:{read:new Set(),written:new Set(),edited:new Set()},settings:{reserveTokens:16384} };
await assert.rejects(compact(preparation,{...router,maxTokens:4096},undefined,undefined,undefined,undefined,undefined,stream),/token cap/);
const result = await compact(preparation,router,undefined,undefined,undefined,undefined,undefined,stream);
assert.match(result.summary,/Synthetic checkpoint/);
assert.equal(seen.at(-1),8192);
await generateSummaryWithUsage(messages,router,16384,undefined,undefined,undefined,undefined,undefined,undefined,stream);
assert.equal(seen.at(-1),13107);
console.log('PASS: bundled model limits match; real Pi compaction uses 8192 / 13107 instead of 4096');
