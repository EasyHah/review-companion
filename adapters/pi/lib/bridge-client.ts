import { spawn, execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { readConfig } from './config.ts';

export const root = fileURLToPath(new URL('../../../', import.meta.url));

export function projectRoot(cwd) {
  try { return execFileSync('git', ['-C', cwd, 'rev-parse', '--show-toplevel'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim(); }
  catch { return path.resolve(cwd); }
}

// Only explicit overrides belong to this adapter. The shared Python runtime
// resolves repository identity, defaults and existing shared records.
export function dataDirectory(_cwd, env = process.env) {
  const value = env.REVIEW_COMPANION_DATA_DIR || readConfig(env).data_dir;
  if (value !== undefined && (typeof value !== 'string' || !value.trim())) throw new Error('data_dir 必须是非空路径');
  return value ? path.resolve(value.replace(/^~(?=\/|$)/, env.HOME || process.env.HOME || '')) : undefined;
}

export class BridgeClient {
  async start(cwd, env = process.env, { host = 'pi' } = {}) {
    this.cwd = projectRoot(cwd);
    // The child does not need any model credentials.
    const childEnv = Object.fromEntries(['PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'REVIEW_COMPANION_CONFIG', 'REVIEW_COMPANION_DATA_DIR', 'REVIEW_COMPANION_GIT', 'REVIEW_COMPANION_EDITOR', 'REVIEW_COMPANION_EMBED_ORIGIN'].filter(k => env[k]).map(k => [k, env[k]]));
    childEnv.PYTHONDONTWRITEBYTECODE = '1';
    const directory = dataDirectory(cwd, env);
    const args = [path.join(root, 'backend/browser_server.py'), '--repository', this.cwd, '--host', host];
    if (directory) args.push('--data-dir', directory);
    this.child = spawn(env.REVIEW_COMPANION_PYTHON || 'python3', args, { env: childEnv, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child.stderr.resume();
    this.child.stdin.on('error', () => {});
    this.connection = await new Promise((resolve, reject) => {
      let buffer = '', settled = false;
      const finish = (error, value) => { if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(value); };
      const timer = setTimeout(() => { this.child.kill(); finish(new Error('回顾服务启动超时')); }, 8000);
      this.child.once('error', () => finish(new Error('无法启动回顾服务，请检查 Python 3.9+ 和 Git')));
      this.child.once('exit', () => { this.connection = null; finish(new Error('回顾服务已退出，请检查 Python 3.9+ 和 Git')); });
      this.child.stdout.on('data', chunk => {
        buffer += chunk.toString();
        if (!settled && buffer.includes('\n')) {
          try {
            const response = JSON.parse(buffer.split('\n')[0]);
            if (response.error) finish(new Error(response.error.message || '回顾服务配置无效'));
            else finish(null, response);
          }
          catch { this.child.kill(); finish(new Error('回顾服务启动响应无效')); }
        }
        if (settled) buffer = '';
      });
    });
    return this;
  }

  async request(endpoint, body, signal) {
    const connection = this.connection;
    if (!connection) throw new Error('回顾服务未连接，请运行 /review 重试');
    const response = await fetch(connection.baseUrl + endpoint, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${connection.agentToken}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(25000)]) : AbortSignal.timeout(25000),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || '回顾服务请求失败');
    return result;
  }

  async switchSession(label) {
    const result = await this.request('/agent/session', { label });
    this.session = result.session;
  }

  call(name, args, signal) {
    return this.request('/agent/tool', { session: this.session, name, arguments: args }, signal);
  }

  get browserUrl() {
    if (!this.connection) throw new Error('回顾服务未连接');
    return `${this.connection.baseUrl}/#token=${this.connection.browserToken}`;
  }

  stop() {
    this.connection = null;
    this.child?.stdin.end();
    const child = this.child;
    const timer = setTimeout(() => { if (child?.exitCode === null) child.kill(); }, 1200);
    timer.unref();
  }
}

export function openBrowser(url) {
  const command = process.platform === 'darwin' ? 'open' : 'xdg-open';
  return new Promise((resolve, reject) => {
    const child = spawn(command, [url], { stdio: 'ignore' });
    child.once('error', () => reject(new Error('无法自动打开浏览器')));
    child.once('exit', code => code === 0 ? resolve() : reject(new Error('无法自动打开浏览器')));
  });
}

// Synchronous check and fill: no await between reading and modifying the editor.
export function fillDraft(ctx, text) {
  if (ctx.mode !== 'tui' || typeof ctx.ui?.getEditorText !== 'function' || typeof ctx.ui?.setEditorText !== 'function') return 'unsupported';
  if (!ctx.isIdle()) return 'busy';
  if (ctx.ui.getEditorText().trim()) return 'occupied';
  ctx.ui.setEditorText(text);
  return 'filled';
}
