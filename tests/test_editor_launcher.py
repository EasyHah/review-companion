from pathlib import Path
import subprocess
import shlex
import tempfile
import unittest
from unittest.mock import patch

from backend.editor_launcher import launch_editor


class EditorLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.file = self.root / 'fixed name; harmless.md'
        self.file.write_text('fixed content\n')
        self.other = self.root / 'other.md'
        self.other.write_text('other version\n')

    def tearDown(self):
        self.temp.cleanup()

    def test_source_handoff_uses_argv_not_supplied_shell_command(self):
        prepared = {'path': str(self.file), 'line': 2, 'command': 'untrusted command must not run'}
        with patch('backend.editor_launcher.find_editor', return_value='/usr/local/bin/code'), patch('backend.editor_launcher.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, b'', b'')
            result = launch_editor(prepared, 'source')
        self.assertTrue(result['launch_requested'])
        self.assertEqual(run.call_args.args[0], ['/usr/local/bin/code', '--reuse-window', '--goto', f'{self.file}:2:1'])
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_diff_handoff_opens_both_exported_versions(self):
        prepared = {'left_path': str(self.file), 'right_path': str(self.other), 'command': 'code --diff prepared files'}
        with patch('backend.editor_launcher.find_editor', return_value='/usr/local/bin/code'), patch('backend.editor_launcher.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, b'', b'')
            result = launch_editor(prepared, 'diff')
        self.assertTrue(result['launch_requested'])
        self.assertEqual(run.call_args.args[0], ['/usr/local/bin/code', '--reuse-window', '--diff', str(self.file), str(self.other)])

    def test_unavailable_editor_leaves_prepared_command_and_reason(self):
        prepared = {'path': str(self.file), 'line': 1, 'command': 'code --goto fixed.md:1:1'}
        with patch('backend.editor_launcher.find_editor', return_value=None), patch('backend.editor_launcher.subprocess.run') as run:
            result = launch_editor(prepared, 'source')
        self.assertFalse(result['launch_requested'])
        self.assertEqual(result['command'], prepared['command'])
        self.assertIn('未找到', result['message'])
        run.assert_not_called()

    def test_launch_error_and_timeout_return_actionable_fallback(self):
        prepared = {'path': str(self.file), 'line': 1, 'command': 'copyable command'}
        cases = [OSError('unavailable'), subprocess.TimeoutExpired('code', 10), subprocess.CompletedProcess([], 7, b'', b'error')]
        for case in cases:
            with self.subTest(case=type(case).__name__), patch('backend.editor_launcher.find_editor', return_value='/usr/local/bin/code'), patch('backend.editor_launcher.subprocess.run') as run:
                if isinstance(case, Exception):
                    run.side_effect = case
                else:
                    run.return_value = case
                result = launch_editor(prepared, 'source')
                self.assertFalse(result['launch_requested'])
                self.assertTrue(result['message'])
                self.assertEqual(result['command'], 'copyable command')

    def test_missing_export_does_not_launch_editor(self):
        prepared = {'path': str(self.root / 'gone.md'), 'line': 1, 'command': 'copyable command'}
        with patch('backend.editor_launcher.find_editor', return_value='/usr/local/bin/code'), patch('backend.editor_launcher.subprocess.run') as run:
            result = launch_editor(prepared, 'source')
        self.assertFalse(result['launch_requested'])
        run.assert_not_called()

    def test_source_and_diff_carry_project_context_in_one_request(self):
        project=self.root/'project with spaces; safe';project.mkdir()
        for mode,prepared,suffix in [
            ('source',{'path':str(self.file),'line':2},['--goto',f'{self.file}:2:1']),
            ('diff',{'left_path':str(self.file),'right_path':str(self.other)},['--diff',str(self.file),str(self.other)]),
        ]:
            with self.subTest(mode=mode),patch('backend.editor_launcher.find_editor',return_value='/usr/local/bin/code'),patch('backend.editor_launcher.subprocess.run') as run:
                run.return_value=subprocess.CompletedProcess([],0,b'',b'')
                result=launch_editor(prepared,mode,repository=str(project))
                self.assertEqual(run.call_count,1)
                expected=['/usr/local/bin/code',str(project),*suffix]
                self.assertEqual(run.call_args.args[0],expected)
                self.assertEqual(shlex.split(result['command']),expected)
                self.assertNotIn('--reuse-window',expected)
                self.assertEqual(result['repository'],str(project))

    def test_project_button_opens_directory_and_invalid_project_never_launches(self):
        with patch('backend.editor_launcher.find_editor',return_value='/usr/local/bin/code'),patch('backend.editor_launcher.subprocess.run') as run:
            run.return_value=subprocess.CompletedProcess([],0,b'',b'')
            result=launch_editor({},'project',repository=str(self.root))
            self.assertTrue(result['launch_requested']);self.assertEqual(run.call_args.args[0],['/usr/local/bin/code',str(self.root)])
            run.reset_mock()
            result=launch_editor({},'project',repository=str(self.root/'missing'))
            self.assertFalse(result['launch_requested']);run.assert_not_called()

    def test_missing_editor_fallback_keeps_project_and_fixed_file(self):
        with patch('backend.editor_launcher.find_editor',return_value=None),patch('backend.editor_launcher.subprocess.run') as run:
            result=launch_editor({'path':str(self.file),'line':1},'source',repository=str(self.root))
        self.assertFalse(result['launch_requested']);run.assert_not_called()
        self.assertEqual(shlex.split(result['command']),['code',str(self.root),'--goto',f'{self.file}:1:1'])


if __name__ == '__main__':
    unittest.main()
