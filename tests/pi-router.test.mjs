import test from 'node:test';
import assert from 'node:assert/strict';
import { createRouter, SIMPLE, COMPLEX } from '../adapters/pi/lib/router.ts';
import { menshenProvider } from '../adapters/pi/lib/menshen.ts';

function context(answer, count = { value: 0 }) {
  return { hasUI: false, modelRegistry: {
    findOfType: () => ({}),
    find: (provider, id) => ({ provider, id }),
    classify: async () => { count.value++; return answer; },
  } };
}
const request = { reason: 'user', messages: [{ role: 'user', content: '你好' }] };
test('JEV simple and complex results select their assigned models', async () => {
  const router = createRouter();
  for (const [choice, id] of [['simple', SIMPLE], ['complex', COMPLEX]]) {
    const result = await router.route(request, context({ stopReason: 'stop', answers: { complexity: { type: 'choice', choice, confidence: 0.9 } } }));
    assert.equal(result.model.id, id);
  }
});
test('tool continuations and retries stay with the model already handling the turn', async () => {
  const count = { value: 0 }, ctx = context(null, count), model = { provider: 'menshen-review', id: SIMPLE };
  for (const reason of ['continuation', 'retry']) {
    const route = await createRouter().route({ ...request, reason, previous: { model } }, ctx);
    assert.equal(route.model, model);
  }
  assert.equal(count.value, 0);
});
test('classification failure or uncertainty falls back to complex without hidden retries', async () => {
  for (const answer of [{ stopReason: 'error' }, { stopReason: 'stop', answers: { complexity: { type: 'choice', choice: 'simple', confidence: 0.2 } } }]) {
    assert.equal((await createRouter().route(request, context(answer))).model.id, COMPLEX);
  }
});
test('image evidence is preserved rather than routed to a text-only model', async () => {
  const messages = [{ role: 'user', content: [{ type: 'image', data: 'synthetic' }] }, ...request.messages];
  assert.equal((await createRouter().route({ ...request, messages }, context({ stopReason: 'error' }))).model.id, SIMPLE);
});
test('cancelled classifier does not start a fallback model request', async () => {
  await assert.rejects(createRouter().route(request, context({ stopReason: 'aborted' })), /取消/);
});
test('shared service preset remains identical without credentials or machine overrides', () => {
  const p = menshenProvider({ MENSHEN_BASE_URL: 'http://unrelated.invalid' });
  assert.equal(p.baseUrl, 'https://menshen-code.test.xdf.cn');
  assert.equal(p.models.length, 2);
  assert.ok(!Object.hasOwn(p, 'apiKey'));
});

test('virtual metadata and physical model limits share explicit overrides', () => {
  const models = menshenProvider({ MENSHEN_CONTEXT_WINDOW:'262144', MENSHEN_MAX_TOKENS:'32768' }).models;
  const router = createRouter(models);
  assert.equal(router.contextWindow,262144);
  assert.equal(router.maxTokens,32768);
  for (const model of models) { assert.equal(model.contextWindow,router.contextWindow); assert.equal(model.maxTokens,router.maxTokens); }
});

test('catalog updates propagate to physical and virtual models without keeping the old 4K cap', () => {
  const models = menshenProvider({}, (_provider,id) => ({ contextWindow:2000000, maxTokens:id==='glm-5.3'?196608:524288 })).models;
  assert.equal(createRouter(models).contextWindow,2000000);
  assert.equal(createRouter(models).maxTokens,196608);
  assert.equal(models[0].maxTokens,524288);
});
