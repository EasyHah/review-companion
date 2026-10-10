"""Real agegr/pi-web HTTP + extension smoke test; synthetic profiles, no model turns."""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

root = Path(__file__).resolve().parents[1]
runtime = Path(os.environ['REVIEW_PI_WEB_RUNTIME']).resolve()
app = Path(os.environ.get('REVIEW_PI_WEB_APP', str(runtime/'node_modules/@agegr/pi-web'))).resolve()
with tempfile.TemporaryDirectory(prefix='review-pi-web-') as temporary:
    folder = Path(temporary).resolve()
    profile = folder / 'profile'; profile.mkdir()
    probe = folder / 'probe.ts'
    probe.write_text('''export default function(pi) {
      pi.registerCommand('existing-sentinel', {description:'Existing command', handler:async()=>{}});
      pi.registerCommand('review-smoke-reload', {handler:async(_,ctx)=>{await ctx.reload();}});
      pi.registerCommand('review-smoke', {handler: async(_,ctx)=>{
        const bridge = globalThis[Symbol.for('review-companion-pi.bridges.v2')].peek(ctx);
        const created = await bridge.call('start_review',{repository_path:ctx.cwd,main_ref:'main'});
        if(created.isError) throw new Error('Synthetic review creation failed');
        const r = created.structuredContent;
        const files = await bridge.call('list_review_files',{review_id:r.review_id});
        const saved = await bridge.call('set_file_review',{review_id:r.review_id,file_id:files.structuredContent.items[0].id,expected_version:r.version,conclusion:'Synthetic change',impact_conclusion:'Synthetic impact'});
        if(saved.isError) throw new Error('Synthetic review write failed');
        pi.sendMessage({customType:'review-smoke',display:true,content:JSON.stringify({smoke:true,reviewId:r.review_id,version:saved.structuredContent.version})},{triggerTurn:false});
      }});
    }''')
    settings = {'packages':[str(root)], 'extensions':[str(probe)], 'defaultProvider':'existing-fixture','defaultModel':'unchanged'}
    (profile/'settings.json').write_text(json.dumps(settings))
    (profile/'models.json').write_text(json.dumps({'providers':{'existing-fixture':{'api':'openai-completions','baseUrl':'http://127.0.0.1:1/v1','apiKey':'synthetic-never-sent','models':[{'id':'unchanged'}]}}}))
    before = {name:(profile/name).read_bytes() for name in ('settings.json','models.json')}
    repos = []
    for name in ('one','two'):
        repo = folder/name;repo.mkdir();repos.append(repo)
        def git(*args): subprocess.run(['git','-C',str(repo),*args],capture_output=True,check=True)
        git('init','-b','main');git('config','user.name','Synthetic');git('config','user.email','synthetic@example.invalid')
        (repo/'example.py').write_text('before\n');git('add','.');git('commit','-m','Before')
        git('checkout','-b','feature');(repo/'example.py').write_text('after\n');git('add','.');git('commit','-m','After')
    config = folder/'config.json'; config.write_text(json.dumps({'data_root':str(folder/'records')}))
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
    env = {k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','TMPDIR')}
    env.update(PI_CODING_AGENT_DIR=str(profile), REVIEW_COMPANION_CONFIG=str(config), PI_OFFLINE='1', PI_WEB_SKIP_VERSION_CHECK='1', NEXT_TELEMETRY_DISABLED='1')
    log = (folder/'server.log').open('w+')
    child = subprocess.Popen(['node',str(app/'bin/pi-web.js'),'--no-open','--hostname','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=log,start_new_session=True)
    base=f'http://127.0.0.1:{port}'
    def request(path, body=None, url=base, token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(url+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
        with urllib.request.urlopen(req,timeout=35) as response:return json.load(response)
    def command(sid, kind, **args):return request('/api/agent/'+sid,{'type':kind,**args})['data']
    def panel(sid):
        command(sid,'prompt',message='/review')
        messages=request('/api/sessions/'+sid)['context']
        urls=re.findall(r'http://127\.0\.0\.1:\d+/#token=[A-Za-z0-9_-]+',json.dumps(messages))
        assert urls,'/review did not publish a panel link'
        return urls[-1]
    def connection(url):
        parts=urllib.parse.urlsplit(url)
        return parts.scheme+'://'+parts.netloc,urllib.parse.parse_qs(parts.fragment)['token'][0]
    try:
        deadline=time.monotonic()+45
        while True:
            try:request('/api/agent/running');break
            except (OSError,ValueError):
                if child.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Pi Web did not start; inspect synthetic server log')
                time.sleep(.25)
        def create(repo):return request('/api/agent/new',{'cwd':str(repo),'type':'ensure_session','toolNames':['read']})
        with concurrent.futures.ThreadPoolExecutor() as pool:
            sessions=list(pool.map(create,[repos[0],repos[0],repos[1]]))
        ids=[s['sessionId'] for s in sessions]
        assert len(set(ids))==3
        for session in sessions:assert session['model']=={'provider':'existing-fixture','modelId':'unchanged'}
        for sid in ids:
            commands=json.dumps(command(sid,'get_commands'))
            assert 'review' in commands and 'existing-sentinel' in commands
            tools=json.dumps(command(sid,'get_tools'))
            assert 'read_fixed_evidence' in tools and 'set_file_review' in tools
        urls=[panel(sid) for sid in ids];assert len(set(urls))==3,'Web sessions shared a bridge'
        connections=[connection(url) for url in urls]
        bindings=[request('/api/session',url=b,token=t) for b,t in connections]
        assert all(b['input_mode']=='copy' for b in bindings)
        if os.environ.get('REVIEW_PI_WEB_SIDEBAR') == '1':
            for sid,url in zip(ids,urls):
                info=request('/api/agent/'+sid+'/review-companion')
                assert info['sessionId']==sid and info['url']==url and info['embedOrigin']==base
                page=urllib.request.urlopen(connection(url)[0]+'/',timeout=3)
                assert 'frame-ancestors '+base+';' in page.headers['Content-Security-Policy']
                page.close()
            try:request('/api/agent/not-a-session/review-companion');raise AssertionError('Unknown session returned a panel')
            except urllib.error.HTTPError as error:assert error.code==404
        for sid in (ids[0],ids[2]):command(sid,'prompt',message='/review-smoke')
        overviews=[]
        for (b,t),binding in zip(connections,bindings):
            result=request('/api/tool',{'session':binding['session'],'name':'read_review','arguments':{}},url=b,token=t)
            assert not result['isError'];overviews.append(result['structuredContent'])
        assert overviews[0]['review_id']==overviews[1]['review_id'],'Same repository lost shared progress'
        assert overviews[0]['review_id']!=overviews[2]['review_id'],'Different repositories shared records'
        assert overviews[1]['progress']['reviewed']==1
        b,t=connections[0]
        try:request('/api/tool',{'session':bindings[1]['session'],'name':'read_review','arguments':{}},url=b,token=t);raise AssertionError('Other session binding accepted')
        except urllib.error.HTTPError as error:assert error.code==409
        try:request('/api/draft',{'session':bindings[0]['session'],'text':'do not insert'},url=b,token=t);raise AssertionError('Web accepted a draft')
        except urllib.error.HTTPError as error:assert json.load(error)['error']['code']=='unsupported'
        # Standard reload stays local to this wrapper, leaves the other sessions bound.
        command(ids[0],'prompt',message='/review-smoke-reload')
        assert panel(ids[0])==urls[0]
        after=request('/api/session',url=b,token=t);assert after['session']!=bindings[0]['session']
        b2,t2=connections[1];assert request('/api/session',url=b2,token=t2)['session']==bindings[1]['session']
        for name,data in before.items():assert (profile/name).read_bytes()==data, name+' changed'
        print(json.dumps({'passed':True,'pi_web':'0.11.0','sessions':3,'same_repo_shared_record':True,'cross_repo_isolation':True,'reload_isolation':True,'copy_only':True,'default_model_and_settings_preserved':True,'model_requests':0,'visible_ui':'user acceptance pending'}))
    except Exception:
        log.flush();log.seek(0);print(log.read()[-6000:])
        raise
    finally:
        os.killpg(child.pid,signal.SIGTERM)
        try:child.wait(timeout=8)
        except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
        log.close()
