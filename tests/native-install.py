"""Verify the shipped local marketplace/package entrypoints without real configuration."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

source=Path(__file__).resolve().parents[1]
version=json.loads((source/'package.json').read_text())['version']
executables={name:shutil.which(name) for name in ('pi','claude','codex')}
assert all(executables.values()), 'Requires the three installed native CLIs'
with tempfile.TemporaryDirectory(prefix='review-install-') as directory:
    root=Path(directory)
    package=root/'release';shutil.copytree(source,package,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (package/'install-plan.json').write_text('{"fixture":"unlisted runtime plan"}')
    (package/'install.log').write_text('unlisted runtime log')
    for name in ('pi','claude','codex'):(root/name).mkdir()
    base={**os.environ,'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_AUTOUPDATER':'1','PI_OFFLINE':'1'}
    def run(host,*args):
        env={**base,'PI_CODING_AGENT_DIR':str(root/'pi'),'CLAUDE_CONFIG_DIR':str(root/'claude'),'CODEX_HOME':str(root/'codex')}
        result=subprocess.run([executables[host],*args],cwd=root,env=env,capture_output=True,text=True,timeout=35)
        assert result.returncode==0, (host,args,result.stdout[-1000:],result.stderr[-1000:])
        return result.stdout
    run('pi','install',str(package))
    settings=json.loads((root/'pi/settings.json').read_text())
    assert len(settings.get('packages',[]))==1
    run('claude','plugin','marketplace','add',str(package))
    run('claude','plugin','install','review-companion@review-companion-unified-local')
    installed=json.loads(run('claude','plugin','list','--json'))
    assert 'review-companion@review-companion-unified-local' in json.dumps(installed)
    run('codex','plugin','marketplace','add',str(package),'--json')
    installed=json.loads(run('codex','plugin','add','review-companion@review-companion-unified-local','--json'))
    assert version in json.dumps(installed)
    print('PASS: shipped Pi package and both self-contained local marketplaces install with in-directory plans/logs in isolated profiles')
