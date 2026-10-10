import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { agentDirectory } from './config.ts';

export function checkDependencies(env = process.env) {
  const checks = [{ name: 'platform', ok: ['darwin','linux'].includes(process.platform), detail: process.platform }];
  for (const [name, executable, args] of [
    ['Node', process.execPath, ['--version']],
    ['Pi', 'pi', ['--version']], ['Python', env.REVIEW_COMPANION_PYTHON || 'python3', ['--version']],
    ['Git', 'git', ['--version']], ['fd', 'fd', ['--version']], ['rg', 'rg', ['--version']],
  ]) {
    const managed = path.join(agentDirectory(env), 'bin', executable);
    const command = ['fd','rg'].includes(name) && fs.existsSync(managed) ? managed : executable;
    const result = spawnSync(command, args, { env, encoding: 'utf8', timeout: 4000 });
    const detail = String(result.stdout || result.stderr || '').trim().split('\n')[0];
    let ok = result.status === 0;
    if (ok && name === 'Node') { const v = detail.match(/v(\d+)\.(\d+)/); ok = !!v && (Number(v[1]) > 22 || Number(v[1]) === 22 && Number(v[2]) >= 19); }
    if (ok && name === 'Python') { const v = detail.match(/(\d+)\.(\d+)/); ok = !!v && (Number(v[1]) > 3 || Number(v[1]) === 3 && Number(v[2]) >= 9); }
    if (ok && name === 'Pi') { const v = detail.match(/^(\d+)\.(\d+)/); ok = !!v && Number(v[1]) === 1 && Number(v[2]) >= 1; }
    checks.push({ name, ok, detail: ok ? detail : '缺失或版本不受支持', command });
  }
  return checks;
}
export function formatDependencies(checks) {
  return checks.map(c => `${c.ok ? '✓' : '✗'} ${c.name}：${c.detail}`).join('\n');
}

// Read Pi's installed catalog without starting Pi or installing dependencies.
export async function builtinModelLookup(env = process.env) {
  const executable = (env.PATH || '').split(path.delimiter).map(dir => path.join(dir, 'pi')).find(file => fs.existsSync(file));
  if (!executable) return undefined;
  let folder = path.dirname(fs.realpathSync(executable));
  while (true) {
    const manifest = path.join(folder, 'package.json');
    if (fs.existsSync(manifest) && JSON.parse(fs.readFileSync(manifest, 'utf8')).name === '@earendil-works/pi-coding-agent') {
      const catalog = path.join(folder, 'node_modules/@earendil-works/pi-ai/dist/providers/all.js');
      if (fs.existsSync(catalog)) return (await import(pathToFileURL(catalog).href)).getBuiltinModel;
      return undefined;
    }
    const parent = path.dirname(folder);
    if (parent === folder) return undefined;
    folder = parent;
  }
}
