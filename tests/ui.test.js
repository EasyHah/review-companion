const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const htmlPath = path.join(__dirname, '..', 'assets', 'app.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

class FakeClassList {
  constructor() { this.values = new Set(); }
  toggle(name, force) { const next = force === undefined ? !this.values.has(name) : force; if (next) this.values.add(name); else this.values.delete(name); return next; }
  add(name) { this.values.add(name); }
  remove(name) { this.values.delete(name); }
  contains(name) { return this.values.has(name); }
}

class FakeElement {
  constructor(tagName = 'div', id = '') {
    this.tagName = tagName.toUpperCase(); this.id = id; this.children = []; this.dataset = {}; this.style = {};
    this.classList = new FakeClassList(); this.attributes = {}; this.listeners = {}; this.disabled = false;
    this.value = ''; this._text = ''; this.selected = false; this.open = this.tagName === 'DETAILS' ? false : undefined;
  }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  focus() { this.focused = true; }
  select() { this.selected = true; }
  closest(selector) { return this.closestTargets && this.closestTargets[selector] ? this.closestTargets[selector] : null; }
}

class FakeDocument {
  constructor() {
    this.readyState = 'complete'; this.visibilityState = 'visible'; this.listeners = {};
    this.documentElement = new FakeElement('html'); this.nodes = new Map();
    for (const id of [
      'status', 'empty-state', 'host-missing', 'content', 'review-selector', 'theme-toggle', 'refresh-button', 'open-project', 'review-title',
      'comparison-line', 'comparison-chips', 'progress-number', 'progress-fill', 'next-card', 'next-path', 'next-meta',
      'next-action', 'feature-list', 'prompt-card', 'prompt-text', 'prompt-context', 'copy-prompt', 'close-prompt',
      'prompt-status', 'external-card', 'external-title', 'external-description', 'external-command', 'copy-command', 'close-external'
    ]) this.nodes.set(id, new FakeElement('div', id));
  }
  getElementById(id) { return this.nodes.get(id); }
  createElement(tagName) { return new FakeElement(tagName); }
  createTextNode(text) { const node = new FakeElement('#text'); node.textContent = text; return node; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}

function descendants(node, predicate) {
  return node.children.flatMap(child => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}
function visibleText(node) {
  if (node.hidden) return '';
  const children = node.tagName === 'DETAILS' && !node.open ? node.children.slice(0, 1) : node.children;
  return node._text + children.map(visibleText).join('');
}
function fileRow(instance) {
  return descendants(instance.document.getElementById('feature-list'), child => (child.className || '').split(' ').includes('file-row'))[0];
}
function toggleDetails(details, open) {
  details.open = open;
  details.listeners.toggle();
}

function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function helpers() {
  const context = { window: {}, navigator: {}, console, Promise, Number, String, Object, Array, Error, setTimeout, clearTimeout };
  context.window.window = context.window;
  vm.runInNewContext(script, context);
  return context.window.ReviewCompanion;
}

function runtime(host, clipboard = { writeText: async () => {} }) {
  const document = new FakeDocument();
  const intervalCallbacks = [];
  const windowListeners = {};
  const context = {
    window: { document, navigator: { clipboard }, openai: host, setInterval: (fn) => { intervalCallbacks.push(fn); return intervalCallbacks.length; }, clearInterval: () => {}, addEventListener: (name, fn) => { windowListeners[name] = fn; } },
    navigator: { clipboard }, console, Promise, Number, String, Object, Array, Error, setTimeout, clearTimeout,
  };
  context.window.window = context.window;
  vm.runInNewContext(script, context);
  return { context, api: context.window.ReviewCompanion, document, intervalCallbacks, windowListeners };
}

function review(overrides = {}) {
  return {
    review_id: 'rv-42', title: '支付流程回顾', version: 7,
    comparison: { repository: 'demo/shop', base_sha: 'abc123', target_sha: 'def456', main_ref: 'main', target_ref: 'feature/pay' },
    features: [{ id: 'payments', title: '支付校验', behavior: '提交前增加库存校验', implementation: '在支付服务入口调用库存检查', total: 1, reviewed: 0 }],
    next_file: { id: 'f-next', path: 'src/payments/check.ts', responsibility: '提交前检查库存' },
    progress: { total: 1, reviewed: 0 }, recent_reviews: [], ...overrides,
  };
}

test('prompt actions carry quoted fixed review scope and select backend next_file', () => {
  const api = helpers();
  const prompt = api.buildPrompt('next', review());
  assert.match(prompt, /"review_id": "rv-42"/);
  assert.match(prompt, /"repository": "demo\/shop"/);
  assert.match(prompt, /"base_sha": "abc123"/);
  assert.match(prompt, /"target_sha": "def456"/);
  assert.match(prompt, /src\/payments\/check\.ts/);
  assert.match(prompt, /f-next/);
  assert.equal(api.nextFileFor(review()).id, 'f-next');
});

test('prompt JSON escapes multiline file paths and next fallback reminds about confirmation', () => {
  const api = helpers();
  const r = review({ next_file: null });
  const prompt = api.buildPrompt('next', r, { id: 'f-1', path: 'src/unsafe\nname.ts' });
  assert.match(prompt, /\\n/);
  assert.doesNotMatch(prompt, /若上一份的回顾状态未能自动保存/);
});

test('explanation includes impact while legacy impact-only prompts stay independent', () => {
  const api = helpers();
  const file = { id: 'f-1', path: 'src/cart.ts' };
  const explanation = api.buildPrompt('explain', review(), file);
  const impact = api.buildPrompt('impact', review(), file);
  assert.match(explanation, /"conclusion_kinds"/);
  assert.match(explanation, /impact_conclusion/);
  assert.match(api.buildPrompt('next', review(), file), /impact_conclusion/);
  assert.match(explanation, /"save_endpoint": "set_file_review"/);
  assert.match(explanation, /"mark_reviewed": true/);
  assert.match(impact, /"conclusion_kind": "impact"/);
  assert.match(impact, /"save_endpoint": "set_file_conclusion"/);
  assert.match(impact, /"mark_reviewed": false/);
  assert.match(impact, /不要修改 reviewed/);
});

test('file actions offer one explanation entry covering changes and impact', async () => {
  const instance = runtime({ callTool: async name => name === 'read_review' ? review() : { review_id:'rv-42', items: [{ id:'f-next',path:'src/cart.ts' }],next_offset:null } });
  await new Promise(resolve => setImmediate(resolve));
  const t=instance.api.__test;t.state.expanded.payments=true;await t.loadFeature('payments',0);
  await new Promise(resolve => setImmediate(resolve));
  const actions=descendants(fileRow(instance), child => child.dataset.action);
  assert.deepEqual(actions.map(button => button.dataset.action), ['explain']);
  assert.equal(actions[0].textContent,'请解释');
});

test('next prompt adds save-failure reminder only after the previous save failed', () => {
  const api = helpers();
  const r = review();
  assert.doesNotMatch(api.buildPrompt('next', r), /未能自动保存/);
  assert.match(api.buildPrompt('next', r, null, { saveFailed: true }), /未能自动保存/);
});

test('explain and impact prompts stay scoped to the requested file', () => {
  const api = helpers();
  const file = { id: 'f-1', path: 'src/cart.ts', old_path: 'src/basket.ts' };
  assert.match(api.buildPrompt('explain', review(), file), /"file_id": "f-1"/);
  assert.match(api.buildPrompt('impact', review(), file), /行为影响/);
  assert.match(api.buildPrompt('explain', review(), file), /"old_path": "src\/basket.ts"/);
});

test('prompt copy never sends a message and exposes fallback text on clipboard failure', async () => {
  let sent = 0;
  const host = { callTool: async () => review(), sendFollowUpMessage: () => { sent += 1; } };
  const failedClipboard = { writeText: async () => { throw new Error('denied'); } };
  const instance = runtime(host, failedClipboard);
  await new Promise((resolve) => setImmediate(resolve));
  instance.api.__test.showPrompt('explain', { id: 'f-1', path: 'src/cart.ts' });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sent, 0);
  assert.match(instance.document.getElementById('prompt-text').value, /src\/cart.ts/);
});

test('Pi draft success is displayed only after input acknowledgement', async () => {
  const instance = runtime({ callTool: async () => review() });
  await new Promise(resolve => setImmediate(resolve));
  const response = deferred();
  instance.context.window.reviewCompanionDraft = () => response.promise;
  instance.api.__test.showPrompt('explain', { id: 'f-1', path: 'src/cart.ts' });
  assert.match(instance.document.getElementById('prompt-status').textContent, /正在填入/);
  response.resolve({ filled: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(instance.document.getElementById('prompt-status').textContent, /等待你发送/);
});

test('occupied Pi editor leaves a selectable question and does not claim success', async () => {
  const instance = runtime({ callTool: async () => review() });
  await new Promise(resolve => setImmediate(resolve));
  instance.context.window.reviewCompanionDraft = async () => { throw new Error('Pi 输入框已有文字，已保留原文'); };
  instance.api.__test.showPrompt('impact', { id: 'f-1', path: 'src/cart.ts' });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(instance.document.getElementById('prompt-status').textContent, /请复制/);
  assert.match(instance.document.getElementById('status').textContent, /已保留原文/);
  assert.match(instance.document.getElementById('prompt-text').value, /src\/cart.ts/);
});

test('successful prompt copy reports copied state', async () => {
  const api = helpers();
  let copied = '';
  const result = await api.copyPrompt('问影响', { writeText: async (text) => { copied = text; } });
  assert.equal(result.copied, true);
  assert.equal(copied, '问影响');
});

test('feature descriptions start closed, expand independently and show each full description once', async () => {
  const behavior = '提交前增加库存校验并阻止超量订单提交';
  const implementation = '在支付服务入口调用库存检查后更新订单状态';
  const r = review({ features: [{ ...review().features[0], behavior, implementation }] });
  const instance = runtime({ callTool: async name => name === 'read_review' ? r : { review_id: 'rv-42', items: [{ id: 'f-next', path: 'src/payments/check.ts' }], next_offset: null } });
  await new Promise(resolve => setImmediate(resolve));
  const list = instance.document.getElementById('feature-list');
  let card = list.children[0];
  let details = card.children.find(child => child.tagName === 'DETAILS');
  const button = card.children.find(child => child.className === 'feature-toggle');
  assert.equal(details.open, false);
  assert.equal(descendants(button, child => ['DETAILS', 'SUMMARY', 'BUTTON'].includes(child.tagName)).length, 0);
  assert.match(visibleText(card), /支付校验/);
  assert.match(visibleText(card), /0\/1/);
  assert.equal(details.children[0].textContent, '功能简介：' + Array.from(behavior + '；' + implementation).slice(0, 15).join('') + '…');
  assert.doesNotMatch(visibleText(card), new RegExp(implementation));
  toggleDetails(details, true);
  assert.equal(visibleText(details.children[0]), '功能简介');
  assert.equal(visibleText(card).split(behavior).length - 1, 1);
  assert.equal(visibleText(card).split(implementation).length - 1, 1);
  assert.doesNotMatch(visibleText(card), /src\/payments/);
  toggleDetails(details, false);
  assert.match(visibleText(details.children[0]), /…$/);
  const t = instance.api.__test; t.state.expanded.payments = true; await t.loadFeature('payments', 0);
  await new Promise(resolve => setImmediate(resolve));
  card = list.children[0]; details = card.children.find(child => child.tagName === 'DETAILS'); toggleDetails(details, true);
  assert.match(visibleText(card), /src\/payments\/check\.ts/);
  assert.equal(visibleText(card).split(implementation).length - 1, 1);
});

test('one conclusion disclosure contains changes and impact without duplicate historical text', async () => {
  const change = '改动内容一二三四五六七八九十一二三四五六';
  const impact = '影响内容仅在当前模块';
  const host = { callTool: async name => name === 'read_review' ? review() : { review_id:'rv-42',items:[{id:'f-next',path:'src/payments/check.ts',reviewed:true,conclusion:change,conclusions:{explanation:change,impact:impact}}],next_offset:null } };
  const instance=runtime(host);await new Promise(resolve=>setImmediate(resolve));
  const t=instance.api.__test;t.state.expanded.payments=true;await t.loadFeature('payments',0);await new Promise(resolve=>setImmediate(resolve));
  const row=fileRow(instance);const notes=row.children.filter(child=>child.tagName==='DETAILS');
  assert.equal(notes.length,1);const note=notes[0];assert.equal(note.open,false);
  assert.match(visibleText(note),/^回顾结论：改动：/);assert.ok(!visibleText(note).includes(impact));
  toggleDetails(note,true);
  assert.equal(visibleText(note.children[0]),'回顾结论');
  assert.equal(visibleText(note),'回顾结论改动：'+change+'影响：'+impact);
  assert.doesNotMatch(visibleText(note),/此前短评/);
  toggleDetails(note,false);assert.match(visibleText(note),/…$/);
});

test('request gate rejects stale responses after a newer selection', () => {
  const api = helpers();
  const gate = api.requestGate();
  const first = gate.next(); const second = gate.next();
  assert.equal(gate.isCurrent(first), false); assert.equal(gate.isCurrent(second), true);
});

test('progress display clamps invalid or over-complete totals and versions never regress', () => {
  const api = helpers();
  assert.equal(api.progressText(2, 4), '2/4'); assert.equal(api.progressPercent(2, 4), 50);
  assert.equal(api.progressPercent(9, 4), 100); assert.equal(api.progressPercent(1, 0), 0);
  assert.equal(api.isNewerOrSame({ version: 8 }, { version: 9 }), false);
});

test('user mark refreshes expanded rows and keeps returned conclusion', async () => {
  let current = review({ version: 1 }); let listCalls = 0; let save;
  const host = { callTool: async (name) => {
    if (name === 'read_review') return current;
    if (name === 'list_review_files') { listCalls += 1; return { review_id: 'rv-42', items: [{ id: 'f-next', path: 'src/payments/check.ts', reviewed: listCalls > 1, responsibility: '提交前检查库存', conclusion: listCalls > 1 ? '库存不足时停止创建订单。' : '' }], total: 1, offset: 0, next_offset: null }; }
    if (name === 'set_file_review') { save = deferred(); return save.promise; }
    throw new Error(`unexpected ${name}`);
  } };
  const instance = runtime(host); await new Promise((resolve) => setImmediate(resolve));
  const t = instance.api.__test; t.state.expanded.payments = true; await t.loadFeature('payments', 0);
  t.markFile('f-next', true); await new Promise((resolve) => setImmediate(resolve)); current = review({ version: 2, progress: { total: 1, reviewed: 1 } });
  save.resolve(current); await new Promise((resolve) => setImmediate(resolve)); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(listCalls, 2); assert.equal(t.state.featureItems.payments[0].reviewed, true); assert.match(t.state.featureItems.payments[0].conclusion, /停止创建订单/);
});

test('external skill update invalidates expanded rows and refreshes a changed conclusion', async () => {
  let current = review({ version: 1 }); let refresh; let listCalls = 0;
  const host = { callTool: async (name) => {
    if (name === 'read_review') { if (refresh) return refresh.promise; return current; }
    if (name === 'list_review_files') { listCalls += 1; return { review_id: 'rv-42', items: [{ id: 'f-next', path: 'src/payments/check.ts', reviewed: true, responsibility: '提交前检查库存', conclusion: listCalls > 1 ? '技能更新后的结论。' : '旧结论。' }], total: 1, offset: 0, next_offset: null }; }
    throw new Error(`unexpected ${name}`);
  } };
  const instance = runtime(host); await new Promise((resolve) => setImmediate(resolve));
  const t = instance.api.__test; t.state.expanded.payments = true; await t.loadFeature('payments', 0); refresh = deferred(); current = review({ version: 2 }); const poll = t.refreshCurrent(true); refresh.resolve(current); await poll; await new Promise((resolve) => setImmediate(resolve));
  assert.equal(listCalls, 2); assert.equal(t.state.featureItems.payments[0].conclusion, '技能更新后的结论。');
});

test('delayed poll cannot reduce the selected record version', async () => {
  let reads = 0; const pendingRefresh = deferred(); const host = { callTool: async (name) => { if (name === 'read_review') { reads += 1; if (reads === 1) return review({ version: 4 }); return pendingRefresh.promise; } throw new Error(`unexpected ${name}`); } };
  const instance = runtime(host); await new Promise((resolve) => setImmediate(resolve));
  const t = instance.api.__test; t.state.review = review({ version: 4 }); t.state.reviewId = 'rv-42'; const refresh = t.refreshCurrent(true); pendingRefresh.resolve(review({ version: 3, progress: { total: 1, reviewed: 0 } })); await refresh;
  assert.equal(t.state.review.version, 4);
});

test('delayed mark response cannot switch back after an explicit review selection', async () => {
  let save; let reads = 0; const second = review({ review_id: 'rv-2', title: '第二次回顾', version: 1 });
  const host = { callTool: async (name, args) => {
    if (name === 'read_review' || name === 'activate_review') { reads += 1; return reads === 1 ? review({ version: 2 }) : second; }
    if (name === 'list_review_files') return { review_id: args.review_id, items: [{ id: 'f-next', path: 'src/payments/check.ts', reviewed: false }], total: 1, offset: 0, next_offset: null };
    if (name === 'set_file_review') { save = deferred(); return save.promise; }
    throw new Error(`unexpected ${name}`);
  } };
  const instance = runtime(host); await new Promise((resolve) => setImmediate(resolve)); const t = instance.api.__test;
  t.state.featureItems.payments = [{ id: 'f-next', path: 'src/payments/check.ts', reviewed: false }];
  t.markFile('f-next', true); await t.loadReview({ review_id: 'rv-2', activate: true });
  save.resolve(review({ version: 3, progress: { total: 1, reviewed: 1 } })); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(t.state.reviewId, 'rv-2'); assert.equal(t.state.review.title, '第二次回顾');
});

test('tool errors preserve version conflict code for deliberate refresh handling', () => {
  const api = helpers();
  assert.throws(() => api.readToolData({ isError: true, code: 'version_conflict' }), (error) => error.code === 'version_conflict');
});

test('a delayed save failure from another review does not add a next-file reminder', async () => {
  const failedSave = deferred();
  const nextReview = review({review_id: 'different-review', version: 1});
  const instance = runtime({callTool: async (name) => {
    if (name === 'read_review') return review();
    if (name === 'activate_review') return nextReview;
    if (name === 'set_file_review') return failedSave.promise;
    throw new Error('unexpected tool');
  }});
  await new Promise(resolve => setImmediate(resolve));
  const t = instance.api.__test;
  t.markFile('f-next', true);
  await t.loadReview({review_id: nextReview.review_id, activate: true});
  failedSave.reject(new Error('old review save failed'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(t.state.reviewId, nextReview.review_id);
  assert.equal(t.state.saveFailed, false);
});

test('cold empty store gives the actionable Chinese message', async () => {
  const instance = runtime({callTool: async () => ({isError: true, structuredContent: {error: {code: 'no_review', message: '请先建立本次分支回顾'}}})});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(instance.api.__test.state.reviewId, null);
  assert.equal(instance.document.getElementById('empty-state').classList.contains('hidden'), false);
  assert.match(instance.document.getElementById('status').textContent, /请先建立/);
});

test('host overview notification applies data without recursive MCP reads', async () => {
  let calls = 0;
  const instance = runtime({callTool: async () => { calls += 1; return review({version: 1}); }});
  await new Promise(resolve => setImmediate(resolve));
  const notification = {detail: {globals: {toolOutput: review({version: 2, progress: {total: 1, reviewed: 1}})}}};
  instance.windowListeners['openai:set_globals'](notification);
  instance.windowListeners['openai:set_globals'](notification);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 1);
  assert.equal(instance.api.__test.state.review.version, 2);
  assert.equal(instance.document.getElementById('progress-number').textContent, '1/1');
});

test('becoming visible reads latest without activating or changing progress', async () => {
  const calls = [];
  const first = review({review_id: 'old'}), latest = review({review_id: 'latest'});
  const instance = runtime({callTool: async (name,args) => { calls.push({name,args}); return calls.length === 1 ? first : latest; }});
  await new Promise(resolve => setImmediate(resolve));
  instance.document.listeners.visibilitychange();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(instance.api.__test.state.reviewId, 'latest');
  assert.equal(calls[1].name, 'read_review');
  assert.equal(Object.keys(calls[1].args).length, 0);
});

test('synchronous host failure is shown instead of aborting panel initialization', async () => {
  const instance = runtime({callTool: () => { throw new Error('连接尚未就绪'); }});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(instance.document.getElementById('status').textContent, /连接尚未就绪/);
  assert.equal(instance.document.getElementById('empty-state').classList.contains('hidden'), false);
});

test('regrouping features discards obsolete expanded IDs without requesting them', async () => {
  let current = review({version: 1});
  const requestedFeatures = [];
  const instance = runtime({callTool: async (name, args) => {
    if (name === 'read_review') return current;
    if (name === 'list_review_files') { requestedFeatures.push(args.feature_id); return {review_id: current.review_id, items: [], next_offset: null}; }
    throw new Error('unexpected tool');
  }});
  await new Promise(resolve => setImmediate(resolve));
  instance.api.__test.state.expanded.payments = true;
  current = review({version: 2, features: [{id: 'new-group', title: '新功能分组', behavior: '变化', implementation: '实现', total: 1, reviewed: 0}]});
  await instance.api.__test.refreshCurrent(true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requestedFeatures.length, 0);
  assert.equal(instance.api.__test.state.expanded.payments, undefined);
  assert.match(instance.document.getElementById('feature-list').textContent, /新功能分组/);
});

test('delegated source and Diff clicks call open_editor with scoped IDs and never open a URL', async () => {
  const calls = []; let opened = 0;
  const instance = runtime({
    callTool: async (name, args) => { calls.push({name, args}); if (name === 'read_review') return review(); if (name === 'list_review_files') return {review_id: 'rv-42', items: [{id: 'f-next', path: 'src/payments/check.ts', reviewed: false, responsibility: '提交前检查库存'}], next_offset: null}; return {launch_requested: true}; },
    openExternal: () => { opened += 1; }
  });
  await new Promise(resolve => setImmediate(resolve));
  const t = instance.api.__test;
  t.state.expanded.payments = true; await t.loadFeature('payments', 0); await new Promise(resolve => setImmediate(resolve));
  const row = fileRow(instance);
  const actions = row.children.find((child) => child.className === 'file-actions');
  const source = actions.children.find((child) => child.dataset.mode === 'source');
  const diff = actions.children.find((child) => child.dataset.mode === 'diff');
  source.closestTargets = {'.js-editor': source}; diff.closestTargets = {'.js-editor': diff};
  instance.document.getElementById('feature-list').listeners.click({target: source});
  assert.equal(source.disabled, true);
  assert.equal(source.textContent, '打开中…');
  await new Promise(resolve => setImmediate(resolve));
  instance.document.getElementById('feature-list').listeners.click({target: diff});
  await new Promise(resolve => setImmediate(resolve));
  const editors = calls.filter((item) => item.name === 'open_editor');
  assert.deepEqual(JSON.parse(JSON.stringify(editors.map((item) => item.args))), [
    {review_id: 'rv-42', file_id: 'f-next', line: 1, mode: 'source'},
    {review_id: 'rv-42', file_id: 'f-next', line: 1, mode: 'diff'},
  ]);
  assert.equal(opened, 0);
});

test('failed open_editor handoff shows actionable message and command fallback', async () => {
  const instance = runtime({
    callTool: async (name) => name === 'read_review' ? review() : {launch_requested: false, message: 'VS Code 启动失败，请手动运行命令。', command: 'code --goto fixed.md:1:1'}
  });
  await new Promise(resolve => setImmediate(resolve));
  instance.api.__test.prepareEditor(review().next_file, 'source');
  await new Promise(resolve => setImmediate(resolve));
  assert.match(instance.document.getElementById('external-description').textContent, /VS Code 启动失败/);
  assert.match(instance.document.getElementById('external-command').textContent, /code --goto fixed.md:1:1/);
  assert.equal(instance.document.getElementById('external-card').classList.contains('hidden'), false);
});

test('unchanged background polling preserves existing feature controls', async () => {
  const instance = runtime({callTool: async () => review()});
  await new Promise(resolve => setImmediate(resolve));
  const featureList = instance.document.getElementById('feature-list');
  const card = featureList.children[0];
  await instance.api.__test.refreshCurrent(true);
  assert.equal(featureList.children[0], card);
});

test('combined long conclusion hides its preview and preserves distinct historical notes', async () => {
  const long = '😀一二三四五六七八九十一二三四五六';
  const instance = runtime({ callTool: async name => name === 'read_review' ? review() : { review_id:'rv-42',items:[{id:'f-next',path:'src/payments/check.ts',conclusion:long+'此前',conclusions:{explanation:long+'解释',impact:long+'影响'}}],next_offset:null } });
  await new Promise(resolve=>setImmediate(resolve));
  const t=instance.api.__test;t.state.expanded.payments=true;await t.loadFeature('payments',0);await new Promise(resolve=>setImmediate(resolve));
  const notes=fileRow(instance).children.filter(child=>child.tagName==='DETAILS');assert.equal(notes.length,1);
  const note=notes[0], summary=note.children[0], preview=summary.children[1];
  assert.equal(preview.hidden,false);assert.equal(Array.from(preview.textContent.slice(1,-1)).length,15);
  toggleDetails(note,true);assert.equal(preview.hidden,true);assert.equal(visibleText(summary),'回顾结论');
  assert.equal(note.children[1].textContent,'改动：'+long+'解释');
  assert.equal(note.children[2].textContent,'影响：'+long+'影响');
  assert.equal(note.children[3].textContent,'此前短评：'+long+'此前');
  toggleDetails(note,false);assert.equal(preview.hidden,false);
});

test('commit subjects are visible while fixed hashes remain in tooltips, labels and copied prompts', async () => {
  const base = 'a'.repeat(40); const target = 'b'.repeat(40);
  const c = { repository: 'demo/shop', base_sha: base, target_sha: target, base_subject: '整理库存检查', target_subject: '修复支付超卖', main_ref: base, target_ref: target };
  const r = review({ title: '修复支付超卖 相对 整理库存检查', comparison: c, recent_reviews: [
    { review_id: 'rv-42', title: '修复支付超卖 相对 整理库存检查', comparison: c },
    { review_id: 'rv-old', title: '旧目标提交 相对 旧基准提交', comparison: { repository: 'demo/shop', base_sha: 'c'.repeat(40), target_sha: 'd'.repeat(40) } },
    { review_id: 'rv-revert', title: '回退 ' + base + ' 的修改 相对 旧基准', comparison: { repository: 'demo/shop', base_sha: 'e'.repeat(40), target_sha: 'f'.repeat(40) } },
  ] });
  const instance = runtime({ callTool: async () => r }); await new Promise(resolve => setImmediate(resolve));
  for (const id of ['review-title', 'comparison-line', 'comparison-chips', 'review-selector']) {
    const container = instance.document.getElementById(id);
    const node = id === 'review-selector' ? container.children[0] : container;
    assert.match(visibleText(node), /修复支付超卖/); assert.match(visibleText(node), /整理库存检查/);
    assert.doesNotMatch(visibleText(node), /[ab]{40}/);
  }
  const heading = instance.document.getElementById('review-title');
  assert.equal(heading.children[0].title, target); assert.equal(heading.children[2].title, base);
  assert.match(heading.children[0].getAttribute('aria-label'), new RegExp(target));
  const chips = instance.document.getElementById('comparison-chips').children;
  assert.equal(chips[0].title, base); assert.equal(chips[1].title, target);
  const selector = instance.document.getElementById('review-selector');
  assert.match(selector.title, new RegExp(base)); assert.match(selector.title, new RegExp(target));
  assert.match(selector.children[0].title, new RegExp(target));
  assert.equal(selector.children[1].textContent, '旧目标提交 相对 旧基准提交');
  assert.match(selector.children[1].title, /c{40}/); assert.match(selector.children[1].title, /d{40}/);
  assert.equal(selector.children[2].textContent, '回退 ' + base + ' 的修改 相对 旧基准');
  assert.match(instance.api.buildPrompt('next', r), new RegExp(base)); assert.match(instance.api.buildPrompt('next', r), new RegExp(target));
});

test('missing subjects use readable placeholders instead of raw hashes', async () => {
  const c = { repository: 'demo/shop', base_sha: 'a'.repeat(40), target_sha: 'b'.repeat(40) };
  const instance = runtime({ callTool: async () => review({ title: c.target_sha + ' 相对 ' + c.base_sha, comparison: c }) });
  await new Promise(resolve => setImmediate(resolve));
  for (const id of ['review-title', 'comparison-line', 'comparison-chips', 'review-selector']) {
    const text = visibleText(instance.document.getElementById(id));
    assert.match(text, /目标提交（说明暂缺）/); assert.match(text, /基准提交（说明暂缺）/); assert.doesNotMatch(text, /[ab]{40}/);
  }
});


test('project button opens only on click, with current review and no state mutation', async () => {
  const calls = [], pending = deferred();
  const instance = runtime({callTool: (name,args) => {calls.push({name,args});return name === 'open_project' ? pending.promise : Promise.resolve(review());}});
  await new Promise(resolve => setImmediate(resolve));
  const button = instance.document.getElementById('open-project');
  assert.equal(button.disabled,false);
  assert.equal(calls.filter(c=>c.name==='open_project').length,0);
  const before = JSON.stringify(instance.api.__test.state.review);
  const action = button.listeners.click();
  instance.api.__test.openProject();
  assert.equal(button.disabled,true);
  await new Promise(resolve => setImmediate(resolve));
  const opens=calls.filter(c=>c.name==='open_project');
  assert.equal(opens.length,1);
  assert.equal(JSON.stringify(opens[0].args),JSON.stringify({review_id:'rv-42'}));
  pending.resolve({launch_requested:true});await action;
  assert.equal(button.disabled,false);
  assert.match(instance.document.getElementById('status').textContent,/已请求 VS Code 打开项目/);
  assert.equal(JSON.stringify(instance.api.__test.state.review),before);
});

test('project button is unavailable without a review and displays fallback on failure', async () => {
  const empty = runtime({callTool:async()=>({review_id:null})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(empty.document.getElementById('open-project').disabled,true);
  const instance=runtime({callTool:async name=>name==='open_project'?{launch_requested:false,command:"code '/project with spaces'",message:'未找到 VS Code'}:review()});
  await new Promise(resolve=>setImmediate(resolve));
  await instance.api.__test.openProject();
  assert.equal(instance.document.getElementById('external-title').textContent,'在 VS Code 打开项目');
  assert.equal(instance.document.getElementById('external-command').textContent,"code '/project with spaces'");
  assert.equal(instance.document.getElementById('open-project').disabled,false);
});

test('late project launch response does not overwrite another selected review', async () => {
  const pending=deferred();
  const instance=runtime({callTool:async(name,args)=>name==='open_project'?pending.promise:review({review_id:args.review_id||'rv-42'})});
  await new Promise(resolve=>setImmediate(resolve));
  const action=instance.api.__test.openProject();
  await instance.api.__test.loadReview({review_id:'rv-other',activate:true});
  const status=instance.document.getElementById('status').textContent;
  pending.resolve({launch_requested:true});await action;
  assert.equal(instance.api.__test.state.reviewId,'rv-other');
  assert.equal(instance.document.getElementById('status').textContent,status);
  assert.equal(instance.document.getElementById('open-project').disabled,false);
});


test('risk notes highlight their file without changing review status or adding workflow buttons', async () => {
  const note = '<img src=x onerror=alert(1)> 返回值待核实';
  const instance = runtime({callTool:async name=>name==='read_review'?review({features:[{...review().features[0],risk_count:1}]}):{review_id:'rv-42',items:[{id:'f-next',path:'src/payments/check.ts',reviewed:true,risk_notes:[note]}],next_offset:null}});
  await instance.api.__test.loadReview({});
  instance.api.__test.state.expanded.payments=true;
  await instance.api.__test.loadFeature('payments',0);
  await new Promise(resolve=>setImmediate(resolve));
  const row=fileRow(instance);
  assert.match(row.className,/has-risk/);
  assert.ok(row.textContent.includes('已回顾'));
  assert.ok(row.textContent.includes(note));
  const actions=descendants(row,node=>node.tagName==='BUTTON');
  assert.deepEqual(actions.map(node=>node.textContent),['请解释','标为未回顾','打开源码','查看 Diff']);
  assert.ok(instance.document.getElementById('feature-list').textContent.includes('1 份文件有风险线索'));
});
