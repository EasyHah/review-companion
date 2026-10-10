import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const MENSHEN_URL = 'https://menshen-code.test.xdf.cn';
export function agentDirectory(env = process.env) {
  const value = env.PI_CODING_AGENT_DIR || path.join(os.homedir(), '.pi', 'agent');
  return path.resolve(value.replace(/^~(?=\/|$)/, os.homedir()));
}
function jsonFile(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') return {};
    throw new Error(`配置文件无法读取：${path.basename(file)}（不显示文件内容）`);
  }
}
export function readConfig(env = process.env) {
  const value = jsonFile(path.join(agentDirectory(env), 'review-companion.json'));
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('review-companion.json 必须是对象');
  const c = value.credential;
  if (c && (!['env', 'file'].includes(c.type) || typeof c.value !== 'string' || !c.value.trim())) throw new Error('credential 需要 type（env/file）和 value');
  if (c?.type === 'file' && (typeof c.field !== 'string' || !c.field.trim())) throw new Error('文件凭证需要 field 字段名');
  return value;
}
export function writeConfig(config, env = process.env) {
  const folder = agentDirectory(env);
  fs.mkdirSync(folder, { recursive: true, mode: 0o700 });
  const file = path.join(folder, 'review-companion.json');
  const temp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(config, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
  fs.renameSync(temp, file);
}
// The host owns login; the package accepts one explicit environment/file reference.
export async function resolveCredential({ env = process.env, credential } = {}) {
  if (env.MENSHEN_API_KEY) return { key: env.MENSHEN_API_KEY, source: 'MENSHEN_API_KEY' };
  if (credential?.key) return { key: credential.key, source: 'Pi /login' };
  const folder = agentDirectory(env), config = readConfig(env);
  const auth = jsonFile(path.join(folder, 'auth.json'));
  if (auth['menshen-review']?.type === 'api_key' && auth['menshen-review'].key)
    return { key: auth['menshen-review'].key, source: 'Pi /login：门神回顾' };
  const selected = config.credential;
  if (selected?.type === 'env') return env[selected.value] ? { key: env[selected.value], source: `环境变量：${selected.value}` } : undefined;
  if (selected?.type === 'file') {
    const file = selected.value.replace(/^~(?=\/|$)/, os.homedir());
    if (!path.isAbsolute(file)) throw new Error('凭证文件必须是绝对路径或 ~/ 路径');
    const key = jsonFile(file)[selected.field];
    return typeof key === 'string' && key ? { key, source: '用户指定的凭证文件' } : undefined;
  }
  return undefined;
}
