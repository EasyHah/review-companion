import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.source=self.root/'release';self.source.mkdir()
        self.manifest={'name':'review-companion','version':'0.3.1'}
        for name in ('package.json','plugin.json','.codex-plugin/plugin.json','.claude-plugin/plugin.json'):
            target=self.source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(self.manifest))
        for name in ('bin/mcp-launch','bin/session-hook','bin/review-pi.mjs'):
            target=self.source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('synthetic executable\n');target.chmod(0o755)
        (self.source/'README.md').write_text('Published instructions\n')
        (self.source/'scripts').mkdir();shutil.copy2(ROOT/'scripts/build_release.py',self.source/'scripts/build_release.py')
        self.files=sorted([p.relative_to(self.source).as_posix() for p in self.source.rglob('*') if p.is_file()]+['release-files.json'])
        self.write_list(self.files)
        self.archive=self.root/'review-companion-0.3.1.zip'
    def tearDown(self):self.temp.cleanup()
    def write_list(self,names):
        (self.source/'release-files.json').write_text(json.dumps({'schema_version':1,'files':names}))
    def run_build(self,*args):
        return subprocess.run([sys.executable,str(self.source/'scripts/build_release.py'),*args],cwd=self.source,capture_output=True,text=True)
    def test_plan_and_logs_inside_release_do_not_change_or_enter_archive(self):
        self.assertEqual(self.run_build().returncode,0);before=self.archive.read_bytes()
        (self.source/'install-plan.json').write_text('{"machine":"private-local-path"}')
        (self.source/'backend').mkdir();(self.source/'backend/install.log').write_text('runtime log')
        result=self.run_build();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(self.archive.read_bytes(),before)
        with zipfile.ZipFile(self.archive) as archive:self.assertEqual(set(archive.namelist()),set(self.files))
    def test_readonly_check_creates_no_plan_archive_or_config(self):
        result=self.run_build('--check');self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(self.archive.exists());self.assertFalse((self.source/'plan.json').exists())
        self.assertTrue(json.loads(result.stdout)['checked'])
    def test_real_published_file_change_changes_digest(self):
        self.assertEqual(self.run_build().returncode,0);before=self.archive.read_bytes()
        (self.source/'README.md').write_text('Changed published instructions\n')
        self.assertEqual(self.run_build().returncode,0);self.assertNotEqual(self.archive.read_bytes(),before)
    def test_missing_declared_file_names_cause_and_preserves_previous_archive(self):
        self.assertEqual(self.run_build().returncode,0);before=self.archive.read_bytes()
        (self.source/'README.md').unlink();result=self.run_build()
        self.assertNotEqual(result.returncode,0);self.assertIn('release_file_missing',result.stderr);self.assertIn('README.md',result.stderr)
        self.assertEqual(self.archive.read_bytes(),before)
    def test_unlisted_symlink_is_ignored_but_declared_symlink_is_rejected(self):
        outside=self.root/'private.txt';outside.write_text('private fixture')
        link=self.source/'extra.txt';link.symlink_to(outside)
        self.assertEqual(self.run_build().returncode,0)
        with zipfile.ZipFile(self.archive) as archive:self.assertNotIn('extra.txt',archive.namelist())
        self.write_list(self.files+['extra.txt']);result=self.run_build()
        self.assertNotEqual(result.returncode,0);self.assertIn('release_symlink',result.stderr)
    def test_traversal_and_duplicate_manifest_entries_are_rejected(self):
        for entries in (self.files+['../outside.txt'],self.files+['README.md']):
            self.write_list(entries);result=self.run_build()
            self.assertNotEqual(result.returncode,0);self.assertIn('invalid_release_manifest',result.stderr)


if __name__=='__main__':unittest.main()
