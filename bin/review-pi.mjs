#!/usr/bin/env node
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { root } from '../adapters/pi/lib/bridge-client.ts';
import { checkDependencies, formatDependencies, builtinModelLookup } from '../adapters/pi/lib/preflight.ts';
import { menshenProvider } from '../adapters/pi/lib/menshen.ts';
import { createRouter } from '../adapters/pi/lib/router.ts';
import { readConfig, writeConfig, resolveCredential } from '../adapters/pi/lib/config.ts';

const env = { ...process.env };
let extra = process.argv.slice(2);
const isolated = extra.includes('--isolated');
extra = extra.filter(arg => arg !== '--isolated');
if (env.REVIEW_PI_AGENT_DIR) env.PI_CODING_AGENT_DIR = env.REVIEW_PI_AGENT_DIR;
else if (isolated) env.PI_CODING_AGENT_DIR = path.join(os.homedir(), '.pi', 'review-companion');
function run(command, args) {
  const child = spawn(command, args, { env, stdio: 'inherit' });
  process.on('SIGINT', () => {});
  process.on('SIGTERM', () => child.kill('SIGTERM'));
  child.once('error', () => { console.error('无法启动命令，请运行 review-pi doctor 检查依赖。'); process.exitCode = 1; });
  child.once('exit', code => { process.exitCode = code ?? 1; });
}
if (extra[0] === 'doctor') {
  const checks = checkDependencies(env);
  console.log(formatDependencies(checks));
  try {
    const models = menshenProvider(env, await builtinModelLookup(env)).models;
    for (const model of [createRouter(models), ...models]) console.log(`${model.id}：上下文 ${model.contextWindow} / 输出 ${model.maxTokens}`);
  } catch (error) { console.log(`模型上限：${error.message}`); }
  try { const auth = await resolveCredential({ env }); console.log(auth ? `门神认证：${auth.source}` : '门神认证：尚未配置，可在 Pi /login 选择门神回顾；其他模型仍可用于回顾'); }
  catch (error) { console.log(`门神认证：${error.message}`); }
  process.exitCode = checks.every(x => x.ok) ? 0 : 1;
} else if (extra[0] === 'configure-auth') {
  const [_, type, value, field] = extra;
  if (!['env','file','auto'].includes(type) || type !== 'auto' && !value || type === 'file' && !field) {
    console.error('用法：review-pi configure-auth auto | env <变量名> | file <绝对路径> <JSON字段名>'); process.exitCode = 1;
  } else {
    const config = readConfig(env);
    if (type === 'auto') delete config.credential;
    else config.credential = { type, value, ...(type === 'file' ? { field } : {}) };
    writeConfig(config, env);
    console.log('已保存认证来源引用，未复制密钥、未修改当前模型。重启 Pi 或 /reload 后生效。');
  }
} else if (extra[0] === 'install') {
  const checks = checkDependencies(env);
  console.log(formatDependencies(checks));
  if (!checks.every(x => x.ok)) { console.error('请先补齐缺失依赖；未修改 Pi 配置，也未启动自动下载。'); process.exitCode = 1; }
  else run('pi', ['install', root]);
} else {
  run('pi', [ '-e', path.join(root,'adapters/pi/extension.ts'), ...['review-companion','branch-risks','behavior-path'].flatMap(name => ['--skill', path.join(root,'skills',name)]), ...extra]);
}
