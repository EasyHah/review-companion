import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from companion_store import ReviewStore, StoreError
from review_companion import Service
from git_snapshot import SnapshotError


def snapshot(directory, count=2, target="b" * 40, long_paths=False):
    return {"repository": str(directory), "main_ref": "main", "main_sha": "a" * 40, "base_sha": "a" * 40, "target_ref": "feature", "target_sha": target, "target_branch": "feature", "files": [{"id": f"{i:064x}", "path": ("文件" * 120 + "/" if long_paths else "src/") + f"file-{i}.py", "old_path": None, "status": "M"} for i in range(count)]}


def concurrent_update(directory, review_id, file_id, queue):
    try:
        ReviewStore(directory).set_file(review_id, file_id, 1, conclusion="已解释")
        queue.put("saved")
    except StoreError as error:
        queue.put(error.code)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ReviewStore(self.root / "state")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **kwargs):
        return self.store.create(snapshot(self.root / "repository", **kwargs))

    def test_new_chat_reads_saved_scope_progress_and_conclusion(self):
        review = self.create()
        file_id = next(iter(review["files"]))
        saved = self.store.set_file(review["review_id"], file_id, 1, conclusion="提交前增加校验")
        restarted = ReviewStore(self.root / "state")
        latest = restarted.overview()
        self.assertEqual(latest["review_id"], review["review_id"])
        self.assertEqual(latest["comparison"]["target_sha"], "b" * 40)
        self.assertEqual(latest["progress"], {"total": 2, "reviewed": 1})
        self.assertEqual(restarted.list_files(review["review_id"])["items"][0]["conclusions"]["explanation"], "提交前增加校验")
        self.assertEqual(latest["version"], saved["version"])
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_rereading_old_record_does_not_steal_recent_focus(self):
        first = self.create()
        second = self.create(target="c" * 40)
        self.store.overview(first["review_id"])
        self.assertEqual(self.store.overview()["review_id"], second["review_id"])
        self.store.overview(first["review_id"], activate=True)
        self.assertEqual(self.store.overview()["review_id"], first["review_id"])
        self.assertEqual(self.store.overview()["version"], 1)

    def test_same_fixed_range_reuses_progress_and_no_stale_write(self):
        first = self.create()
        file_id = next(iter(first["files"]))
        self.store.set_file(first["review_id"], file_id, 1, conclusion="短结论")
        same = self.create()
        self.assertEqual(same["review_id"], first["review_id"])
        self.assertEqual(same["version"], 2)
        with self.assertRaises(StoreError) as error:
            self.store.set_file(first["review_id"], file_id, 1, reviewed=False)
        self.assertEqual(error.exception.code, "version_conflict")
        self.assertEqual(self.store.overview()["progress"]["reviewed"], 1)

    def test_two_processes_cannot_overwrite_each_other(self):
        review = self.create()
        identifiers = list(review["files"])
        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        workers = [context.Process(target=concurrent_update, args=(str(self.root / "state"), review["review_id"], identifier, queue)) for identifier in identifiers]
        for process in workers:
            process.start()
        outcomes = [queue.get(timeout=10) for _ in workers]
        for process in workers:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertCountEqual(outcomes, ["saved", "version_conflict"])
        current = self.store.raw_review(review["review_id"])
        pending = next(item for item in current["files"].values() if not item["reviewed"])
        self.store.set_file(review["review_id"], pending["id"], current["version"])
        self.assertEqual(self.store.overview()["progress"]["reviewed"], 2)

    def test_overview_keeps_progress_and_shared_file_counts_once(self):
        review = self.create()
        one, two = list(review["files"])
        self.store.set_file(review["review_id"], one, 1, conclusion="已有结论")
        result = self.store.set_overview(review["review_id"], 2, [{"title": "提交校验", "behavior": "提交前校验", "implementation": "增加检查入口", "file_ids": [one], "responsibilities": {one: "校验入口"}}, {"title": "错误反馈", "behavior": "直接提示失败", "implementation": "复用检查结果", "file_ids": [one, two]}])
        self.assertTrue(result["overview_ready"])
        self.assertEqual(result["progress"], {"total": 2, "reviewed": 1})
        self.assertEqual([f["reviewed"] for f in result["features"]], [1, 1])
        self.assertEqual(self.store.list_files(review["review_id"])["items"][0]["conclusions"]["explanation"], "已有结论")
        self.assertEqual(result["next_file"]["id"], two)

    def test_bad_membership_does_not_partially_change_roles(self):
        review = self.create()
        file_id = next(iter(review["files"]))
        original = self.store.path.read_bytes()
        valid = {"title": "入口", "behavior": "变化", "implementation": "实现", "file_ids": [file_id], "responsibilities": {file_id: "改后的职责"}}
        invalid = {"title": "错误", "behavior": "变化", "implementation": "实现", "file_ids": ["not-this-snapshot"]}
        with self.assertRaises(StoreError):
            self.store.set_overview(review["review_id"], 1, [valid, invalid])
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_large_file_catalog_is_delivered_without_loss(self):
        review = self.create(count=120, long_paths=True)
        identifiers = list(review["files"])
        self.store.set_overview(review["review_id"], 1, [{"title": "文件目录", "behavior": "改动概括", "implementation": "入口调整", "file_ids": identifiers, "responsibilities": {identifier: "职责" * 150 for identifier in identifiers}}])
        delivered, offset, page_count = [], 0, 0
        while True:
            page = self.store.list_files(review["review_id"], offset=offset, limit=100)
            self.assertLess(len(json.dumps(page, ensure_ascii=False).encode()), 95_000)
            delivered += [item["id"] for item in page["items"]]
            page_count += 1
            if page["next_offset"] is None:
                break
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
        self.assertEqual(delivered, identifiers)
        self.assertGreater(page_count, 1)

    def test_corrupt_and_unknown_schema_files_are_preserved(self):
        self.store.directory.mkdir()
        for payload in (b"broken json", b'{"schema_version":42,"reviews":{}}'):
            self.store.path.write_bytes(payload)
            with self.assertRaises(StoreError):
                self.create()
            self.assertEqual(self.store.path.read_bytes(), payload)

    def test_failed_atomic_replace_preserves_previous_record(self):
        review = self.create()
        before = self.store.path.read_bytes()
        with patch("companion_store.os.replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                self.store.set_file(review["review_id"], next(iter(review["files"])), 1)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.store.directory.glob(".reviews-*")), [])

    def test_empty_snapshot_is_already_complete(self):
        self.create(count=0)
        result = self.store.overview()
        self.assertTrue(result["overview_ready"])
        self.assertEqual(result["progress"], {"total": 0, "reviewed": 0})
        self.assertIsNone(result["next_file"])

    def test_explanation_impact_and_preexisting_note_do_not_overwrite(self):
        review = self.create()
        first, second = list(review['files'])
        state = json.loads(self.store.path.read_text())
        state['reviews'][review['review_id']]['files'][first]['conclusion'] = '此前保存的短评'
        self.store.path.write_text(json.dumps(state, ensure_ascii=False))
        updated = self.store.set_file(review['review_id'], first, 1, conclusion='解释结论')
        self.assertEqual(self.store.raw_review(review['review_id'])['files'][first]['conclusion'], '此前保存的短评')
        updated = self.store.set_conclusion(review['review_id'], first, updated['version'], 'impact', '影响结论')
        row = self.store.list_files(review['review_id'])['items'][0]
        self.assertEqual(row['conclusions'], {'explanation': '解释结论', 'impact': '影响结论'})
        self.assertTrue(row['reviewed'])
        before = self.store.path.read_bytes()
        with self.assertRaises(StoreError):
            self.store.set_conclusion(review['review_id'], first, 1, 'impact', '过期写入')
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.set_conclusion(review['review_id'], second, updated['version'], 'impact', '仅分析影响')
        self.assertFalse(self.store.raw_review(review['review_id'])['files'][second]['reviewed'])
        self.assertEqual(self.store.overview()['progress']['reviewed'], 1)

    def test_combined_explanation_saves_both_notes_in_one_version(self):
        review=self.create();fid=next(iter(review['files']))
        saved=self.store.set_file(review['review_id'],fid,1,conclusion='改动',impact_conclusion='影响')
        self.assertEqual(saved['version'],2)
        row=self.store.list_files(review['review_id'])['items'][0]
        self.assertTrue(row['reviewed']);self.assertEqual(row['conclusions'],{'explanation':'改动','impact':'影响'})
        before=self.store.path.read_bytes()
        with self.assertRaises(StoreError):self.store.set_file(review['review_id'],fid,2,conclusion='不应写入',impact_conclusion='x'*501)
        self.assertEqual(self.store.path.read_bytes(),before)
        with self.assertRaises(StoreError):self.store.set_file(review['review_id'],fid,1,conclusion='过期',impact_conclusion='过期')
        self.assertEqual(self.store.path.read_bytes(),before)
        self.store.set_file(review['review_id'],fid,2,reviewed=False)
        self.assertEqual(self.store.list_files(review['review_id'])['items'][0]['conclusions'],row['conclusions'])

    def test_existing_asset_records_are_hidden_without_rewriting_saved_data(self):
        original = snapshot(self.root / 'repository', count=3)
        original['files'][0].update(path='assets/new-logo.svg', status='M')
        original['files'][1].update(path='assets/config.json', status='M')
        original['files'][2].update(path='assets/deleted-logo.png', status='D')
        review = self.store.create(original)
        first = original['files'][0]['id']
        self.store.set_file(review['review_id'], first, 1, conclusion='资源原记录')
        before = self.store.path.read_bytes()
        summary = self.store.overview()
        self.assertEqual(summary['progress'], {'total': 2, 'reviewed': 0})
        self.assertEqual(summary['next_file']['id'], original['files'][1]['id'])
        page = self.store.list_files(review['review_id'])
        self.assertEqual([f['path'] for f in page['items']], ['assets/config.json', 'assets/deleted-logo.png'])
        self.assertEqual(summary['features'][0]['total'], 2)
        self.assertEqual(summary['recent_reviews'][0]['total'], 2)
        self.assertEqual(summary['excluded_resource_count'], 1)
        self.assertEqual(self.store.path.read_bytes(), before)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        (self.repo / "orders.py").write_text("before\n", encoding="utf-8")
        self.git("add", ".")
        self.commit("base")
        self.git("checkout", "-b", "feature")
        (self.repo / "orders.py").write_text("committed change\nsecond line\n", encoding="utf-8")
        self.git("add", ".")
        self.commit("feature")
        (self.repo / "orders.py").write_text("uncommitted workspace\n", encoding="utf-8")
        self.service = Service(self.root / "state")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    def commit(self, message):
        self.git("-c", "user.name=Companion Test", "-c", "user.email=companion@example.test", "commit", "-m", message)

    def test_public_tools_continue_review_and_export_fixed_source(self):
        review = self.service.dispatch("start_review", {"repository_path": str(self.repo)})
        file = review["first_files"]["items"][0]
        review = self.service.dispatch("set_review_overview", {"review_id": review["review_id"], "expected_version": review["version"], "features": [{"title": "订单检查", "behavior": "提交前检查", "implementation": "补充检查入口", "file_ids": [file["id"]], "responsibilities": {file["id"]: "订单提交说明"}}]})
        self.service.dispatch("set_file_review", {"review_id": review["review_id"], "file_id": file["id"], "expected_version": review["version"], "conclusion": "提交前增加检查"})
        another_chat = Service(self.root / "state")
        resumed = another_chat.dispatch("read_review", {})
        self.assertEqual(resumed["progress"], {"total": 1, "reviewed": 1})
        prepared = another_chat.dispatch("prepare_editor", {"review_id": review["review_id"], "file_id": file["id"], "line": 2})
        self.assertEqual(Path(prepared["path"]).read_text(), "committed change\nsecond line\n")
        self.assertEqual(prepared["commit_sha"], review["comparison"]["target_sha"])
        self.assertNotIn("uncommitted", json.dumps(prepared))
        same = another_chat.dispatch("start_review", {"repository_path": str(self.repo)})
        self.assertEqual(same["review_id"], review["review_id"])
        self.assertEqual(same["progress"]["reviewed"], 1)

    def test_only_explicit_open_tool_renders_a_panel(self):
        by_name = {tool['name']: tool for tool in self.service.tools()}
        self.assertNotIn('resourceUri', by_name['read_review']['_meta']['ui'])
        self.assertNotIn('openai/outputTemplate', by_name['read_review']['_meta'])
        self.assertIn('resourceUri', by_name['open_review']['_meta']['ui'])
        for name, tool in by_name.items():
            if name != 'open_review':
                self.assertNotIn('openai/outputTemplate', tool['_meta'])

    def test_overview_length_error_identifies_exact_field_without_writing(self):
        review = self.service.dispatch('start_review', {'repository_path': str(self.repo)})
        fid = review['first_files']['items'][0]['id']
        feature = {'title': 'Feature', 'behavior': 'Change', 'implementation': 'x' * 483, 'file_ids': [fid]}
        before = self.service.store.path.read_bytes()
        result = self.service.rpc('tools/call', {'name': 'set_review_overview', 'arguments': {'review_id': review['review_id'], 'expected_version': review['version'], 'features': [feature]}})
        error = result['structuredContent']['error']
        self.assertEqual(error['field'], 'arguments.features[0].implementation')
        self.assertEqual((error['actual_length'], error['max_length']), (483, 240))
        self.assertEqual(self.service.store.path.read_bytes(), before)

    def test_overview_bad_id_reports_location_and_can_be_corrected_once(self):
        review = self.service.dispatch('start_review', {'repository_path': str(self.repo)})
        fid = review['first_files']['items'][0]['id']
        typo = ('0' if fid[0] != '0' else '1') + fid[1:]
        feature = {'title': 'Feature', 'behavior': 'Change', 'implementation': 'Entry', 'file_ids': [typo]}
        args = {'review_id': review['review_id'], 'expected_version': review['version'], 'features': [feature]}
        before = self.service.store.path.read_bytes()
        result = self.service.rpc('tools/call', {'name': 'set_review_overview', 'arguments': args})
        error = result['structuredContent']['error']
        self.assertEqual(error['field'], 'features[0].file_ids[0]')
        self.assertEqual(error['invalid_file_id'], typo)
        self.assertEqual(self.service.store.path.read_bytes(), before)
        feature['file_ids'] = [fid]
        saved = self.service.dispatch('set_review_overview', args)
        self.assertEqual(saved['version'], review['version'] + 1)
        self.assertEqual(saved['progress']['reviewed'], 0)

    def test_current_and_recent_labels_use_fixed_commit_subjects(self):
        review = self.service.dispatch('start_review', {'repository_path': str(self.repo)})
        self.assertEqual(review['comparison']['base_subject'], 'base')
        self.assertEqual(review['comparison']['target_subject'], 'feature')
        self.assertEqual(review['title'], 'feature 相对 base')
        recent = review['recent_reviews'][0]
        self.assertEqual(recent['title'], 'feature 相对 base')
        self.assertEqual(recent['comparison']['target_sha'], review['comparison']['target_sha'])
        self.assertEqual(recent['comparison']['base_sha'], review['comparison']['base_sha'])

    def test_existing_sha_titles_are_enriched_without_rewriting_records(self):
        review = self.service.dispatch('start_review', {'repository_path': str(self.repo)})
        state = json.loads(self.service.store.path.read_text())
        stored = state['reviews'][review['review_id']]
        stored['title'] = stored['comparison']['target_sha'] + ' 相对 ' + stored['comparison']['base_sha']
        stored['comparison']['target_ref'] = stored['comparison']['target_sha']
        stored['comparison']['main_ref'] = stored['comparison']['base_sha']
        self.service.store.path.write_text(json.dumps(state))
        before = self.service.store.path.read_bytes()
        presented = self.service.dispatch('read_review', {'review_id': review['review_id']})
        self.assertEqual(presented['title'], 'feature 相对 base')
        self.assertEqual(presented['version'], review['version'])
        self.assertEqual(presented['comparison']['target_sha'], stored['comparison']['target_sha'])
        self.assertEqual(self.service.store.path.read_bytes(), before)

    def test_missing_commit_metadata_does_not_break_saved_review(self):
        review = self.service.dispatch('start_review', {'repository_path': str(self.repo)})
        before = self.service.store.path.read_bytes()
        with patch('review_companion.read_commit_subject', side_effect=SnapshotError('git_error', 'repository temporarily unavailable')):
            presented = self.service.dispatch('read_review', {'review_id': review['review_id']})
        self.assertEqual(presented['progress'], review['progress'])
        self.assertIsNone(presented['comparison']['base_subject'])
        self.assertIsNone(presented['comparison']['target_subject'])
        self.assertIn('说明暂不可用', presented['title'])
        self.assertNotIn(review['comparison']['target_sha'], presented['title'])
        self.assertEqual(self.service.store.path.read_bytes(), before)

    def test_impact_tool_preserves_explanation_and_unreviewed_state(self):
        review = self.service.dispatch('start_review', {'repository_path': str(self.repo)})
        file = review['first_files']['items'][0]
        saved = self.service.dispatch('set_file_conclusion', {'review_id': review['review_id'], 'file_id': file['id'], 'expected_version': review['version'], 'conclusion_kind': 'impact', 'conclusion': '影响结论'})
        self.assertEqual(saved['progress']['reviewed'], 0)
        self.service.dispatch('set_file_review', {'review_id': review['review_id'], 'file_id': file['id'], 'expected_version': saved['version'], 'conclusion': '解释结论'})
        row = self.service.dispatch('list_review_files', {'review_id': review['review_id']})['items'][0]
        self.assertEqual(row['conclusions'], {'explanation': '解释结论', 'impact': '影响结论'})

    def test_editor_actions_handoff_fixed_source_and_both_diff_sides(self):
        review = self.service.dispatch('start_review', {'repository_path': str(self.repo)})
        file = review['first_files']['items'][0]
        for mode in ('source', 'diff'):
            with patch('review_companion.launch_editor') as launcher:
                launcher.side_effect = lambda prepared, selected_mode, **kwargs: {**prepared, 'launch_requested': True}
                result = self.service.dispatch('open_editor', {'review_id': review['review_id'], 'file_id': file['id'], 'mode': mode})
                prepared, actual_mode = launcher.call_args.args
                self.assertEqual(actual_mode, mode)
                self.assertEqual(launcher.call_args.kwargs, {"repository": str(self.repo.resolve())})
                self.assertTrue(result['launch_requested'])
                right = prepared['path'] if mode == 'source' else prepared['right_path']
                self.assertEqual(Path(right).read_text(), 'committed change\nsecond line\n')
                if mode == 'diff':
                    self.assertEqual(Path(prepared['left_path']).read_text(), 'before\n')

    def test_stdio_handshake_resources_tools_and_errors(self):
        panel_uri = next(tool for tool in self.service.tools() if tool['name'] == 'open_review')['_meta']['ui']['resourceUri']
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "start_review", "arguments": {"repository_path": str(self.repo)}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_review", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": panel_uri}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "read_review", "arguments": {"activate": "yes"}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "old_ai_pipeline", "arguments": {}}},
        ]
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, str(ROOT / "backend" / "review_companion.py"), "mcp", "--data-dir", str(self.root / "wire-state")], input="\n".join(json.dumps(message) for message in messages) + "\n", text=True, capture_output=True, env=environment, timeout=15, check=True)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses], list(range(1, 8)))
        tools = responses[1]["result"]["tools"]
        self.assertEqual(len(tools), 13)
        self.assertIn('read_fixed_evidence', {tool['name'] for tool in tools})
        self.assertTrue(next(tool for tool in tools if tool['name'] == 'read_review')['annotations']['readOnlyHint'])
        self.assertEqual(responses[2]["result"]["structuredContent"]["comparison"]["repository"], str(self.repo.resolve()))
        self.assertEqual(responses[3]["result"]["structuredContent"]["progress"]["total"], 1)
        self.assertIn("<html", responses[4]["result"]["contents"][0]["text"])
        self.assertEqual(responses[4]["result"]["contents"][0]['uri'], panel_uri)
        self.assertTrue(responses[5]["result"]["isError"])
        self.assertEqual(responses[6]["result"]["structuredContent"]["error"]["code"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
