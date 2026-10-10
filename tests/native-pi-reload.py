"""Actual Pi /reload regression against a temporary copy of this package. No model calls."""
import json,os,queue,shutil,subprocess,tempfile,threading
from pathlib import Path

source=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='review-reload-') as directory:
    root=Path(directory);package=root/'package';shutil.copytree(source,package)
    extension=package/'adapters/pi/extension.ts'
    observer=root/'observer.ts'
    observer.write_text('''export default function(pi) {
      pi.registerCommand('reload-test',{handler:async(_,ctx)=>{await ctx.reload();}});
      pi.on('session_start',(_,ctx)=>{
        const model=ctx.modelRegistry.find('menshen-review','glm-5.3');
        const auto=ctx.modelRegistry.find('menshen-review','auto');
        const bridge=globalThis[Symbol.for('review-companion-pi.bridges.v2')]?.peek(ctx);
        ctx.ui.notify(JSON.stringify({probe:true,reason:_.reason,physical:model?.contextWindow,virtual:auto?.contextWindow,current:ctx.model?.contextWindow,base:bridge?.connection?.baseUrl,session:bridge?.session}), 'info');
      });
    }''')
    profile=root/'profile';profile.mkdir()
    (profile/'settings.json').write_text(json.dumps({'defaultProvider':'menshen-review','defaultModel':'auto'}))
    # Override the lookup only in the fixture copy; changing this imported .ts
    # module must affect the already-running Pi after /reload.
    limits=package/'adapters/pi/lib/menshen.ts';original=limits.read_text()
    before=original.replace('export function menshenProvider(env = process.env, lookup) {',"export function menshenProvider(env = process.env, lookup) { env = {...env, MENSHEN_CONTEXT_WINDOW:'262144', MENSHEN_MAX_TOKENS:'16384'};")
    limits.write_text(before)
    env={**os.environ,'PI_CODING_AGENT_DIR':str(profile),'REVIEW_COMPANION_DATA_DIR':str(root/'records'),'MENSHEN_API_KEY':'synthetic-never-sent','PI_OFFLINE':'1'}
    child=subprocess.Popen(['pi','--mode','rpc','--no-context-files','--no-skills','--no-extensions','-e',str(extension),'-e',str(observer)],cwd=root,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    events=queue.Queue()
    def reader():
        for line in child.stdout:
            try:events.put(json.loads(line))
            except ValueError:pass
    threading.Thread(target=reader,daemon=True).start()
    def snapshot():
        while True:
            event=events.get(timeout=15)
            if event.get('type')=='extension_ui_request' and event.get('method')=='notify':
                try:data=json.loads(event.get('message',''))
                except ValueError:continue
                if data.get('probe'):return data
    try:
        first=snapshot();assert first['physical']==first['virtual']==first['current']==262144, first
        assert first.get('base') and first.get('session'), 'native bridge did not start'
        limits.write_text(original)
        child.stdin.write(json.dumps({'type':'prompt','message':'/reload-test'})+'\n');child.stdin.flush()
        second=snapshot();assert second['reason']=='reload', second
        assert second['physical']==second['virtual']==second['current']==1000000, second
        assert second['base']==first['base'], 'reload replaced browser bridge'
        assert second['session']!=first['session'], 'reload retained stale binding'
        print('PASS: actual Pi /reload refreshes imported limits, physical model, virtual model and active session to 1M; browser bridge survives with a fresh binding')
    finally:
        child.stdin.close()
        try:child.wait(timeout=8)
        except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=5)
