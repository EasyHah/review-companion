import { MENSHEN_URL } from './config.ts';
// Pi 1.1.0 catalog snapshots; the extension supplies a live bundled-catalog lookup.
const CATALOG_DEFAULTS = {
  'deepseek-v4.1-flash': { provider: 'opencode', contextWindow: 1000000, maxTokens: 384000 },
  'glm-5.3': { provider: 'zai', contextWindow: 1000000, maxTokens: 131072 },
};
export function modelLimits(id, env = process.env, lookup) {
  const snapshot = CATALOG_DEFAULTS[id];
  const catalog = lookup?.(snapshot.provider, id) || snapshot;
  const contextWindow = Number(env.MENSHEN_CONTEXT_WINDOW || catalog.contextWindow);
  const maxTokens = Number(env.MENSHEN_MAX_TOKENS || catalog.maxTokens);
  if (!Number.isSafeInteger(contextWindow) || !Number.isSafeInteger(maxTokens) || maxTokens < 1 || contextWindow <= maxTokens) throw new Error('门神上下文和输出上限配置无效');
  return { contextWindow, maxTokens };
}
// Shared service preset; credential resolution is separate and owned by Pi.
export function menshenProvider(env = process.env, lookup) {
  return {
    baseUrl: MENSHEN_URL,
    api: 'anthropic-messages',
    models: [{ id: 'deepseek-v4.1-flash', name: '门神 · DeepSeek（简单 / 图片）', reasoning: false,
      input: ['text', 'image'], ...modelLimits('deepseek-v4.1-flash', env, lookup),
      // Pi requires cost metadata. Zero is an unconfigured estimate, not free service.
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } },
      { id: 'glm-5.3', name: '门神 · GLM（复杂任务）', reasoning: false,
        input: ['text'], ...modelLimits('glm-5.3', env, lookup),
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
  };
}
