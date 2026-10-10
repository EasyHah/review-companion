import fs from 'node:fs';
import path from 'node:path';
import { fillDraft, openBrowser, root } from './lib/bridge-client.ts';
import { bridgeRegistry } from './lib/bridge-registry.ts';
import { menshenProvider } from './lib/menshen.ts';
import { createRouter, JUDGE } from './lib/router.ts';
import { builtinProviders, getBuiltinModel } from '@earendil-works/pi-ai/providers/all';
import { createProvider } from '@earendil-works/pi-ai';
import { resolveCredential } from './lib/config.ts';
import { checkDependencies, formatDependencies } from './lib/preflight.ts';

export default function reviewCompanion(pi) {
  const provider = menshenProvider(process.env, getBuiltinModel);
  {
    const builtins = builtinProviders();
    const typesafe = builtins.find(p => p.id === 'typesafe');
    const anthropic = builtins.find(p => p.id === 'anthropic');
    if (!typesafe?.classify) throw new Error('当前 Pi 缺少 TypeSafe 分类实现，请使用 Pi 1.1.0');
    const auth = {
      name: '门神回顾 API Key',
      login: async interaction => ({ type: 'api_key', key: await interaction.prompt({ type: 'secret', message: '请输入门神 API Key（由 Pi 保存）' }) }),
      resolve: async ({ credential, signal }) => {
        signal.throwIfAborted();
        const found = await resolveCredential({ credential });
        if (!found) return undefined;
        return { auth: { apiKey: found.key, headers: { Authorization: `Bearer ${found.key}`, 'x-api-key': found.key } }, source: found.source };
      },
    };
    pi.registerProvider(createProvider({
      id: 'menshen-review', name: '门神回顾', auth: { apiKey: auth },
      models: provider.models.map(model => ({ ...model, api: provider.api, provider: 'menshen-review', baseUrl: provider.baseUrl })),
      api: { stream: (...args) => anthropic.stream(...args), streamSimple: (...args) => anthropic.streamSimple(...args) },
    }));
    pi.registerProvider(createProvider({
      id: 'menshen-judge', name: '门神回顾分类', auth: { apiKey: { resolve: auth.resolve } },
      models: [{ type: 'classifier', provider: 'menshen-judge', id: JUDGE, name: '门神 · JEV 任务分流', api: 'typesafe-system-one',
        baseUrl: provider.baseUrl + '/v1', input: ['text'], contextWindow: 16000,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }],
      classifiers: { 'typesafe-system-one': { classify: (model, context, options) => typesafe.classify(model, context, options) } },
    }));
    pi.registerVirtualModel(createRouter(provider.models));
  }
  const specs = JSON.parse(fs.readFileSync(path.join(root, 'backend/tool-specs.json'), 'utf8'));
  const registry = bridgeRegistry();
  let client, context, timer, epoch = 0, polling = false;

  async function ensure(ctx) {
    client = await registry.ensure(ctx);
    return client;
  }

  function showProgress(ctx, progress) {
    if (ctx.hasUI) ctx.ui.setStatus(ctx.mode === 'tui' ? 'review-companion' : 'command:/review',
      progress ? `回顾 ${progress.reviewed}/${progress.total} · /review` : '回顾面板 /review');
  }

  async function poll() {
    if (polling || !client?.connection || !context) return;
    polling = true;
    const owner = client, ctx = context, current = epoch, session = client.session;
    try {
      const result = await owner.request('/agent/poll', { session });
      if (epoch !== current || owner !== client || ctx !== context || !result.draft) return;
      let status;
      try { status = fillDraft(ctx, result.draft.text); } catch { status = 'failed'; }
      await owner.request('/agent/ack', { session, id: result.draft.id, status });
    } catch { /* Browser keeps the question available and offers copy fallback. */ }
    finally { polling = false; }
  }

  async function showPanel(ctx) {
    const current = await ensure(ctx);
    if (ctx.mode !== 'tui') {
      pi.sendMessage({ customType: 'review-companion-panel', content: `[打开回顾总览](${current.browserUrl})\n\n在回顾面板复制问题，粘贴到当前聊天后自行发送。本入口适用于 Pi Web 与回顾服务在同一台电脑运行。`, display: true }, { triggerTurn: false });
      return;
    }
    try { await openBrowser(current.browserUrl); }
    catch { ctx.ui.notify(`请在浏览器打开：${current.browserUrl}`, 'warning'); }
  }

  for (const spec of specs) {
    pi.registerTool({
      ...spec, label: spec.name,
      async execute(_id, params, signal, _onUpdate, ctx) {
        const owner = await ensure(ctx);
        let result = await owner.call(spec.name, params, signal);
        if (spec.name === 'open_review' && !result.isError && ctx.mode !== 'tui') {
          const data = { ...result.structuredContent, browser_url: owner.browserUrl, input_mode: 'copy' };
          result = { ...result, structuredContent: data, content: [{ type: 'text', text: JSON.stringify(data) }] };
        }
        if (result.isError) throw new Error(`[${result.structuredContent?.error?.code || 'failed'}] ${result.structuredContent?.error?.message || '回顾操作失败'}`);
        const progress = result.structuredContent?.progress;
        showProgress(ctx, progress);
        if (spec.name === 'open_review' && ctx.mode === 'tui') await showPanel(ctx);
        return { content: result.content, details: result.structuredContent };
      },
    });
  }

  pi.registerCommand('review', {
    description: '打开浏览器回顾面板；只准备问题，由你在 Pi 中发送',
    handler: async (args, ctx) => {
      if (args.trim() === 'doctor') {
        const version = JSON.parse(fs.readFileSync(path.join(root,'package.json'),'utf8')).version;
        const limits = ['auto','deepseek-v4.1-flash','glm-5.3'].map(id => {
          const model = ctx.modelRegistry.find('menshen-review',id);
          return `${id}：上下文 ${model?.contextWindow ?? '未加载'} / 输出 ${model?.maxTokens ?? '未加载'}`;
        });
        ctx.ui.notify(`回顾插件 ${version}\n${limits.join('\n')}\n${formatDependencies(checkDependencies())}`, 'info');
        return;
      }
      await showPanel(ctx);
    },
  });

  pi.registerCommand('review-config', {
    description: '显示门神认证来源与配置提示，不改变当前模型',
    handler: async (_args, ctx) => {
      const found = await resolveCredential();
      ctx.ui.notify(found ? `认证来源：${found.source}。当前模型保持不变；需要分流时用 /model 选择“门神回顾自动分流”。` : '尚无门神认证。可设置 MENSHEN_API_KEY，或 /login 选择“门神回顾”录入。回顾工具仍可使用当前模型。', found ? 'info' : 'warning');
    },
  });

  pi.on('session_start', async (_event, ctx) => {
    epoch++; context = ctx;
    clearInterval(timer);
    try {
      const owner = await ensure(ctx);
      await owner.switchSession(ctx.sessionManager.getSessionId());
      const result = await owner.call('open_review', {}); // data read only; never opens a browser here
      const view = result.structuredContent;
      showProgress(ctx, view?.progress);
      if (ctx.mode === 'tui') { timer = setInterval(poll, 400); timer.unref(); }
    } catch (error) { if (ctx.hasUI) ctx.ui.notify(error.message, 'error'); }
  });

  pi.on('session_shutdown', async (event, ctx) => {
    epoch++; context = undefined;
    clearInterval(timer);
    registry.release(ctx, event.reason);
    client = undefined;
  });
}
