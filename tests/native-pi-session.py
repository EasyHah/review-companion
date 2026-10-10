"""Installed Pi lifecycle integration; synthetic credentials, no model requests."""
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import urllib.request
import urllib.error

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='pi-lifecycle-') as folder:
    folder = Path(folder)
    profile=folder/'profile';profile.mkdir()
    sentinel=folder/'existing.ts';sentinel.write_text("export default pi => pi.registerCommand('existing-sentinel',{description:'keep',handler:async()=>{}})")
    (profile/'settings.json').write_text(json.dumps({'defaultProvider':'existing-fixture','defaultModel':'unchanged','extensions':[str(sentinel)]}))
    (profile/'models.json').write_text(json.dumps({'providers':{'existing-fixture':{'api':'openai-completions','baseUrl':'https://example.invalid/v1','apiKey':'synthetic','models':[{'id':'unchanged'}]}}}))
    original_settings=(profile/'settings.json').read_bytes()
    observer = folder / 'observer.ts'
    observer.write_text('''export default function(pi) {
      pi.on('session_start', async (_,ctx) => {
        const c = globalThis[Symbol.for('review-companion-pi.bridges.v2')]?.peek(ctx);
        ctx.ui.notify(JSON.stringify({testBridge:true,base:c?.connection?.baseUrl,
          token:c?.connection?.browserToken,session:c?.session,model:ctx.model?.id,commands:pi.getCommands().map(c=>c.name),tools:pi.getAllTools().map(t=>t.name)}), 'info');
      });
    }''')
    env = {**os.environ, 'MENSHEN_API_KEY':'synthetic-never-sent', 'REVIEW_PI_AGENT_DIR':str(folder/'profile'), 'REVIEW_COMPANION_DATA_DIR':str(folder/'records')}
    child = subprocess.Popen(['node',str(root/'bin/review-pi.mjs'),'--mode','rpc','--no-context-files','-e',str(observer)], cwd=folder,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    events = queue.Queue()
    def read():
        for line in child.stdout:
            try: events.put(json.loads(line))
            except ValueError: pass
    threading.Thread(target=read,daemon=True).start()
    def snapshot():
        while True:
            e=events.get(timeout=15)
            if e.get('type')=='extension_ui_request' and e.get('method')=='notify':
                try: data=json.loads(e.get('message',''))
                except ValueError: continue
                if data.get('testBridge'): return data
    try:
        first=snapshot()
        assert first['model']=='unchanged', 'plugin changed current model'
        assert 'existing-sentinel' in first['commands'], 'existing extension was disabled'
        assert 'set_file_risks' in first['tools'], 'risk tool missing'
        for skill in ('review-companion','branch-risks','behavior-path'):
            assert any(skill in name for name in first['commands']), ('skill missing',skill,first['commands'])
        assert (profile/'settings.json').read_bytes()==original_settings, 'plugin rewrote user settings'
        assert first.get('base') and first.get('session'), 'native extension did not start'
        child.stdin.write(json.dumps({'type':'new_session'})+'\n');child.stdin.flush()
        second=snapshot()
        assert first['base']!=second['base'], 'RPC replacement reused another session bridge'
        assert first['session']!=second['session'], 'old binding survived session replacement'
        request=urllib.request.Request(second['base']+'/api/draft',data=json.dumps({'session':first['session'],'text':'stale'}).encode(),headers={'Authorization':'Bearer '+second['token'],'Content-Type':'application/json'})
        try: urllib.request.urlopen(request,timeout=3);raise AssertionError('stale draft accepted')
        except urllib.error.HTTPError as error: assert error.code==409
        print('PASS: existing model/extensions/settings preserved; RPC /new gets an isolated URL and rejects stale bindings')
    finally:
        child.stdin.close()
        try: child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.terminate();child.wait(timeout=8)
