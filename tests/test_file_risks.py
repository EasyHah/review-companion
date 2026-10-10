import copy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from companion_store import StoreError
from review_companion import Service
from test_companion import snapshot
from host_mcp import HostService


class FileRiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.service=Service(self.root/'data')
        self.record=self.service.store.create(snapshot(self.root))
        self.rid=self.record['review_id'];self.ids=list(self.record['files'])

    def save(self,updates,version=None):
        view=self.service.store.overview(self.rid)
        return self.service.dispatch('set_file_risks',{'review_id':self.rid,'expected_version':version or view['version'],'updates':updates})

    def test_partial_updates_clear_only_selected_file_and_preserve_progress(self):
        before=self.service.store.overview(self.rid)
        saved=self.save([{'file_id':fid,'notes':['L2：返回值待核实']} for fid in self.ids])
        self.assertEqual(saved['progress'],before['progress']);self.assertEqual(saved['features'][0]['risk_count'],2)
        cleared=self.save([{'file_id':self.ids[0],'notes':[]}])
        files=self.service.store.list_files(self.rid)['items']
        self.assertEqual(files[0]['risk_notes'],[]);self.assertEqual(files[1]['risk_notes'],['L2：返回值待核实'])
        self.assertEqual(cleared['features'][0]['risk_count'],1)
        restored=Service(self.root/'data').store.list_files(self.rid)['items']
        self.assertEqual(restored,files)

    def test_invalid_batch_and_version_conflict_are_atomic(self):
        before=self.service.store.path.read_bytes()
        for updates in [[{'file_id':self.ids[0],'notes':['valid']},{'file_id':'unknown','notes':['bad']}],
                        [{'file_id':self.ids[0],'notes':['x'*301]}],
                        [{'file_id':self.ids[0],'notes':['']}],
                        [{'file_id':self.ids[0],'notes':[]}]*2]:
            with self.assertRaises(StoreError):self.save(updates)
            self.assertEqual(before,self.service.store.path.read_bytes())
        self.save([{'file_id':self.ids[0],'notes':['note']}])
        with self.assertRaises(StoreError):self.save([{'file_id':self.ids[0],'notes':[]}],1)

    def test_regroup_preserves_file_marks_and_shows_in_each_group(self):
        saved=self.save([{'file_id':self.ids[0],'notes':['核实调用方']}])
        feature={'title':'一','behavior':'行为','implementation':'实现','file_ids':self.ids}
        view=self.service.dispatch('set_review_overview',{'review_id':self.rid,'expected_version':saved['version'],'features':[feature,{**feature,'title':'二'}]})
        self.assertEqual([f['risk_count'] for f in view['features']],[1,1])
        self.assertEqual(view['progress']['total'],2)

    def test_legacy_analysis_maps_without_rewriting_and_documents_stay_hidden(self):
        def legacy(state):
            rec=state['reviews'][self.rid]
            rec['risk_overview']={'risks':[{'title':'旧风险','feature_id':rec['features'][0]['id'],'evidence':[{'path':rec['files'][self.ids[0]]['path'],'side':'target'}]}]}
            rec['behavior_path']={'legacy':'retained'}
            rec['files']['doc']={'id':'doc','path':'README.md','status':'M','reviewed':True,'conclusion':''}
            rec['features'][0]['file_ids'].append('doc')
        self.service.store._locked(legacy,write=True);before=self.service.store.path.read_bytes()
        view=self.service.store.overview(self.rid);files=self.service.store.list_files(self.rid)['items']
        self.assertEqual(files[0]['risk_notes'],['旧风险']);self.assertEqual(view['progress']['total'],2)
        self.assertNotIn('risk_overview',view);self.assertNotIn('behavior_path',view)
        self.assertEqual(before,self.service.store.path.read_bytes())
        with self.assertRaises(StoreError):self.save([{'file_id':'doc','notes':['文档不标记']}])
        with self.assertRaisesRegex(StoreError,'文档文件'):
            self.service.dispatch('read_fixed_evidence',{'review_id':self.rid,'file_id':'doc'})
        self.save([{'file_id':self.ids[0],'notes':[]}])
        self.assertEqual(self.service.store.list_files(self.rid)['items'][0]['risk_notes'],[])
        self.assertIn('risk_overview',self.service.store.raw_review(self.rid))

    def legacy_record(self, same_group=False, missing_group=False):
        def apply(state):
            rec=state['reviews'][self.rid]
            rec['features']=[{'id':'caller','title':'调用方','file_ids':self.ids if same_group else [self.ids[0]]}]
            if not same_group:rec['features'].append({'id':'storage','title':'存储层','file_ids':[self.ids[1]]})
            rec['risk_overview']={'risks':[{'title':'调用方忽略返回值','feature_id':'gone' if missing_group else 'caller',
                'evidence':[{'path':rec['files'][fid]['path'],'side':'target','reason':'问题位置' if i==0 else '上下文'} for i,fid in enumerate(self.ids)]}]}
        self.service.store._locked(apply,write=True)

    def test_legacy_context_in_other_feature_is_not_highlighted(self):
        self.legacy_record();before=self.service.store.path.read_bytes()
        files=self.service.store.list_files(self.rid)['items'];view=self.service.store.overview(self.rid)
        self.assertEqual(files[0]['risk_notes'],['调用方忽略返回值'])
        self.assertEqual(files[1]['risk_notes'],[])
        self.assertEqual([f['risk_count'] for f in view['features']],[1,0])
        self.assertEqual(before,self.service.store.path.read_bytes())

    def test_legacy_ambiguous_or_missing_feature_does_not_guess(self):
        for same_group,missing_group in [(True,False),(False,True)]:
            self.legacy_record(same_group,missing_group)
            self.assertTrue(all(not f['risk_notes'] for f in self.service.store.list_files(self.rid)['items']))
        self.save([{'file_id':self.ids[1],'notes':['明确绑定的新风险线索']}])
        self.assertEqual(self.service.store.list_files(self.rid)['items'][1]['risk_notes'],['明确绑定的新风险线索'])

    def test_legacy_evidence_matches_the_correct_rename_side(self):
        self.legacy_record()
        def rename(state):
            rec=state['reviews'][self.rid];item=rec['files'][self.ids[0]]
            item.update(path='new.py',old_path='old.py',status='R')
            rec['risk_overview']['risks'][0]['evidence']=[{'path':'old.py','side':'target'}]
        self.service.store._locked(rename,write=True)
        self.assertEqual(self.service.store.list_files(self.rid)['items'][0]['risk_notes'],[])
        def base_side(state):state['reviews'][self.rid]['risk_overview']['risks'][0]['evidence'][0]['side']='base'
        self.service.store._locked(base_side,write=True)
        self.assertEqual(self.service.store.list_files(self.rid)['items'][0]['risk_notes'],['调用方忽略返回值'])

    def test_codex_inventory_is_lightweight_and_browser_cannot_mark(self):
        host=HostService('codex',self.root,config={},data_dir=str(self.root/'data'));self.addCleanup(host.close)
        names={t['name'] for t in host.rpc('tools/list',{})['tools']}
        self.assertIn('set_file_risks',names)
        self.assertFalse({'set_risk_overview','set_behavior_path','read_context_evidence'}&names)
        with self.assertRaises(StoreError):host.bridge.dispatch('set_file_risks',{},browser=True)
        other=self.root/'other';other.mkdir();foreign=HostService('codex',other,config={},data_dir=str(self.root/'data'));self.addCleanup(foreign.close)
        result=foreign.rpc('tools/call',{'name':'set_file_risks','arguments':{'review_id':self.rid,'expected_version':1,'updates':[{'file_id':self.ids[0],'notes':['x']}]}})
        self.assertTrue(result['isError']);self.assertEqual(result['structuredContent']['error']['code'],'wrong_repository')
