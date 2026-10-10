import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from browser_server import Bridge,create_server
from companion_store import StoreError
from host_mcp import HostService
from runtime import data_directory


class SharedHostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve();self.repo=self.root/'repo';self.repo.mkdir()
        self.git('init','-b','main');self.git('config','user.name','Synthetic');self.git('config','user.email','synthetic@example.invalid')
        (self.repo/'flow.py').write_text('before\n');self.git('add','.');self.git('commit','-m','Before')
        self.git('checkout','-b','feature');(self.repo/'flow.py').write_text('after\n');self.git('add','.');self.git('commit','-m','After')
        self.config={'data_root':str(self.root/'data'),'_home':str(self.root/'home')}
        self.claude=HostService('claude',self.repo,self.config)
        self.codex=HostService('codex',self.repo,self.config)
        self.pi=Bridge(data_directory(self.repo,config=self.config),self.repo,'pi')
    def git(self,*args):return subprocess.run(['git',*args],cwd=self.repo,capture_output=True,check=True).stdout
    def tearDown(self):self.claude.close();self.codex.close();self.temp.cleanup()
    def call(self,host,name,args={}):return host.rpc('tools/call',{'name':name,'arguments':args})
    def test_pi_to_claude_to_codex_share_one_record(self):
        review=self.pi.dispatch('start_review',{'repository_path':str(self.repo),'main_ref':'main'})['structuredContent']
        rid=review['review_id'];fid=review['first_files']['items'][0]['id']
        saved=self.call(self.claude,'set_file_review',{'review_id':rid,'file_id':fid,'expected_version':review['version'],'conclusion':'解释','impact_conclusion':'初始影响'})['structuredContent']
        self.assertEqual(saved['version'],review['version']+1)
        self.assertEqual(self.pi.service.store.list_files(rid)['items'][0]['conclusions']['impact'],'初始影响')
        self.call(self.codex,'set_file_conclusion',{'review_id':rid,'file_id':fid,'expected_version':saved['version'],'conclusion_kind':'impact','conclusion':'影响'})
        restored=self.pi.dispatch('read_review',{})['structuredContent']
        self.assertEqual(restored['review_id'],rid);self.assertEqual(restored['progress']['reviewed'],1)
        item=self.pi.service.store.list_files(rid)['items'][0]
        self.assertEqual(item['conclusions'],{'explanation':'解释','impact':'影响'})
        self.assertEqual(self.claude.bridge.service.store.path,self.codex.bridge.service.store.path)
        self.assertIsNone(self.claude.server);self.assertIsNone(self.codex.server)
    def test_fixed_evidence_is_paged_and_ignores_worktree(self):
        review=self.pi.dispatch('start_review',{'repository_path':str(self.repo),'main_ref':'main'})['structuredContent']
        args={'review_id':review['review_id'],'file_id':review['first_files']['items'][0]['id'],'mode':'diff','limit':10}
        text='';offset=0
        while True:
            result=self.call(self.codex,'read_fixed_evidence',{**args,'offset':offset})['structuredContent'];text+=result['text']
            if result['next_offset'] is None:break
            offset=result['next_offset']
        self.assertIn('before',text);self.assertIn('after',text)
        (self.repo/'flow.py').write_text('WORKTREE CHANGED')
        fresh=self.call(self.claude,'read_fixed_evidence',{k:v for k,v in args.items() if k!='limit'})['structuredContent']
        self.assertEqual(fresh['text'],text)
        self.assertTrue(self.call(self.codex,'read_fixed_evidence',{**args,'path':'/etc/passwd'})['isError'])
    def test_file_risks_roundtrip_across_all_hosts(self):
        view=self.pi.dispatch('start_review',{'repository_path':str(self.repo),'main_ref':'main'})['structuredContent']
        rid=view['review_id'];fid=view['first_files']['items'][0]['id']
        saved=self.pi.dispatch('set_file_risks',{'review_id':rid,'expected_version':view['version'],'updates':[{'file_id':fid,'notes':['Pi 核实后保留的线索']} ]})
        self.assertFalse(saved['isError'])
        for host in (self.claude,self.codex):
            files=self.call(host,'list_review_files',{'review_id':rid})['structuredContent']['items']
            self.assertEqual(files[0]['risk_notes'],['Pi 核实后保留的线索'])
        current=self.call(self.codex,'read_review',{'review_id':rid})['structuredContent']
        updated=self.call(self.codex,'set_file_risks',{'review_id':rid,'expected_version':current['version'],'updates':[{'file_id':fid,'notes':['Codex 补充后待核实']} ]})['structuredContent']
        web=Bridge(data_directory(self.repo,config=self.config),self.repo,'pi_web')
        self.assertEqual(web.dispatch('list_review_files',{'review_id':rid})['structuredContent']['items'][0]['risk_notes'],['Codex 补充后待核实'])
        cleared=self.call(self.claude,'set_file_risks',{'review_id':rid,'expected_version':updated['version'],'updates':[{'file_id':fid,'notes':[]} ]})['structuredContent']
        self.assertEqual(cleared['progress'],view['progress'])
        restored=self.pi.dispatch('read_review',{'review_id':rid})['structuredContent']
        self.assertEqual(restored['features'][0]['risk_count'],0)
    def test_both_mcp_hosts_are_copy_only_and_do_not_expose_editor(self):
        for host in (self.claude,self.codex):
            tools=self.call_tools(host)
            self.assertEqual(len(tools),12);self.assertNotIn('open_editor',[t['name'] for t in tools]);self.assertNotIn('open_project',[t['name'] for t in tools])
            self.assertTrue(all('_meta' not in t for t in tools))
            bind=self.call(host,'connect_session',{'repository_path':str(self.repo)})['structuredContent']
            self.assertEqual(bind['input_mode'],'copy')
            with self.assertRaises(StoreError):host.bridge.route('/api/draft',{'session':bind['session'],'text':'do not send'})
            self.assertTrue(self.call(host,'open_editor',{})['isError'])
            self.assertTrue(self.call(host,'open_project',{})['isError'])
    def test_project_action_uses_record_scope_and_preserves_progress(self):
        review=self.pi.dispatch('start_review',{'repository_path':str(self.repo),'main_ref':'main'})['structuredContent']
        before=self.pi.service.store.path.read_bytes()
        with patch('review_companion.launch_editor',return_value={'launch_requested':True}) as launcher:
            result=self.pi.dispatch('open_project',{'review_id':review['review_id']},browser=True)
            self.assertTrue(result['structuredContent']['launch_requested'])
            launcher.assert_called_once_with({},'project',repository=str(self.repo))
            launcher.reset_mock()
            for extra in ({'repository':str(self.root)},{'path':'/tmp'}):
                self.assertTrue(self.pi.dispatch('open_project',{'review_id':review['review_id'],**extra},browser=True)['isError'])
            launcher.assert_not_called()
        self.assertEqual(before,self.pi.service.store.path.read_bytes())

    def call_tools(self,host):return host.rpc('tools/list',{})['tools']
    def test_bind_invalidates_old_requests_and_explicit_open_returns_http(self):
        first=self.call(self.codex,'connect_session',{'session_id':'one'})['structuredContent']
        opened=self.call(self.codex,'open_review')['structuredContent']
        self.assertTrue(opened['browser_url'].startswith('http://127.0.0.1:'))
        self.call(self.codex,'connect_session',{'session_id':'two'})
        with self.assertRaises(StoreError):self.codex.bridge.require_session(first['session'])
    def test_duplicate_http_headers_are_rejected(self):
        server=create_server(self.pi);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=[('Host',server.address),('Authorization','Bearer '+self.pi.browser_token),('Content-Type','application/json'),('Content-Length','2')]
        try:
            for extra,expected in [([('Host',server.address)],403),([('Authorization','Bearer '+self.pi.browser_token)],401),([('Content-Length','2')],400),([('Transfer-Encoding','chunked')],400),([('Origin','http://'+server.address),('Origin','http://'+server.address)],400)]:
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                conn.putrequest('POST','/api/tool',skip_host=True)
                for key,value in base+extra:conn.putheader(key,value)
                conn.endheaders(b'{}');response=conn.getresponse();response.read();self.assertEqual(response.status,expected);conn.close()
        finally:server.shutdown();server.server_close();thread.join(timeout=2)


class DataDirectoryTests(unittest.TestCase):
    def test_unique_old_directory_is_reused_and_conflict_is_explicit(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);repo=home/'repo';digest=hashlib.sha256(str(repo.resolve()).encode()).hexdigest()
            pi=home/'.local/share/review-companion-pi'/digest[:24];pi.mkdir(parents=True);(pi/'reviews.json').write_text('{}')
            self.assertEqual(data_directory(repo,environ={'HOME':str(home)}),pi.resolve())
            claude=home/'.local/share/review-companion-claude/projects'/digest[:20];claude.mkdir(parents=True);(claude/'reviews.json').write_text('{}')
            with self.assertRaises(StoreError):data_directory(repo,environ={'HOME':str(home)})
            self.assertEqual(data_directory(repo,config={'_home':str(home),'project_data_dirs':{str(repo):str(pi)}},environ={}),pi.resolve())
            self.assertEqual((pi/'reviews.json').read_text(),'{}')
    def test_exact_override_is_not_treated_as_root(self):
        with tempfile.TemporaryDirectory() as temp:
            expected=Path(temp)/'existing'
            self.assertEqual(data_directory(temp,str(expected),environ={}),expected.resolve())

if __name__=='__main__':unittest.main()
