from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend.git_snapshot import (
    SnapshotError,
    collect_snapshot,
    export_diff,
    export_source,
    read_blob,
    read_commit_subject,
)


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.write("modified.txt", "before\n")
        self.write("deleted.txt", "delete me\n")
        self.write("old name.txt", "rename me\n")
        self.write("same.txt", "same\n")
        self.git("add", ".")
        self.commit("base")
        self.base = self.rev("HEAD")
        self.git("branch", "feature")
        self.git("checkout", "feature")
        self.write("modified.txt", "committed target\n")
        self.repo.joinpath("deleted.txt").unlink()
        self.repo.joinpath("old name.txt").rename(self.repo / "new name.txt")
        self.write("added $(touch SHOULD_NOT_EXIST).txt", "new\n")
        self.write("unicodé\tline\nname.txt", "special filename\n")
        self.write("binary.bin", b"\x00\x01\xff")
        self.git("add", "-A")
        self.commit("feature changes")
        self.target = self.rev("HEAD")
        self.git("checkout", "main")
        self.write("main-only.txt", "main branch\n")
        self.git("add", ".")
        self.commit("main diverges")
        self.main = self.rev("HEAD")
        self.git("checkout", "feature")
        self.write("modified.txt", "workspace only\n")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def rev(self, ref):
        return self.git("rev-parse", ref).stdout.decode().strip()

    def commit(self, message):
        self.git("-c", "user.name=Snapshot Test", "-c", "user.email=snapshot@example.test", "commit", "-m", message)

    def write(self, name, contents):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            path.write_bytes(contents)
        else:
            path.write_text(contents, encoding="utf-8")


class SnapshotTests(GitFixture):
    def test_commit_subject_uses_the_fixed_commit_not_current_branch(self):
        self.assertEqual(read_commit_subject(str(self.repo), self.base), 'base')
        self.assertEqual(read_commit_subject(str(self.repo), self.target), 'feature changes')
        self.assertEqual(read_commit_subject(str(self.repo), self.main), 'main diverges')
        self.write('modified.txt', 'committed target\n')
        self.git('checkout', 'main')
        self.assertEqual(read_commit_subject(str(self.repo), self.target), 'feature changes')

    def test_commit_subject_rejects_moving_refs_and_caches_fixed_metadata(self):
        with self.assertRaises(SnapshotError):
            read_commit_subject(str(self.repo), 'HEAD')
        import backend.git_snapshot as module
        with patch('backend.git_snapshot._run_git', wraps=module._run_git) as command:
            first = read_commit_subject(str(self.repo), self.target)
            second = read_commit_subject(str(self.repo), self.target)
            self.assertEqual(first, second)
            self.assertEqual(command.call_count, 1)

    def test_collect_uses_merge_base_and_parses_all_changes(self):
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        self.assertEqual(snapshot["main_sha"], self.main)
        self.assertEqual(snapshot["target_sha"], self.target)
        self.assertEqual(snapshot["base_sha"], self.base)
        self.assertEqual(snapshot["target_branch"], "feature")
        by_path = {item["path"]: item for item in snapshot["files"]}
        self.assertEqual(by_path["modified.txt"]["status"], "M")
        self.assertEqual(by_path["deleted.txt"]["status"], "D")
        self.assertEqual(by_path["new name.txt"]["status"], "R")
        self.assertEqual(by_path["new name.txt"]["old_path"], "old name.txt")
        self.assertEqual(by_path["added $(touch SHOULD_NOT_EXIST).txt"]["status"], "A")
        self.assertEqual(by_path["unicodé\tline\nname.txt"]["status"], "A")
        self.assertEqual(snapshot["status_counts"]["M"], 1)
        for item in snapshot["files"]:
            self.assertRegex(item["id"], r"^[0-9a-f]{16,64}$")

    def test_blob_reads_commit_even_when_worktree_differs(self):
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        modified = next(item for item in snapshot["files"] if item["path"] == "modified.txt")
        data, sha, path = read_blob(snapshot, modified)
        self.assertEqual(data, b"committed target\n")
        self.assertEqual(sha, self.target)
        self.assertEqual(path, "modified.txt")

    def test_export_is_readonly_and_safe_for_filename_injection(self):
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        item = next(item for item in snapshot["files"] if item["path"].startswith("added "))
        root = Path(self.tmp.name) / "exports"
        result = export_source(snapshot, item, root)
        path = Path(result["path"])
        self.assertEqual(path.read_bytes(), b"new\n")
        self.assertEqual(stat_mode(path) & 0o222, 0)
        self.assertIn("code --reuse-window --goto", result["command"])
        self.assertTrue(result["vscode_uri"].startswith("vscode://file/"))
        self.assertFalse((self.repo / "SHOULD_NOT_EXIST").exists())
        rerun = export_source(snapshot, item, root)
        self.assertEqual(rerun["path"], result["path"])

    def test_binary_source_is_rejected_for_editor_export(self):
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        item = next(item for item in snapshot["files"] if item["path"] == "binary.bin")
        with self.assertRaisesRegex(SnapshotError, "binary"):
            export_source(snapshot, item, Path(self.tmp.name) / "exports")

    def test_intermediate_export_symlink_cannot_escape_root(self):
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        item = next(item for item in snapshot["files"] if item["path"] == "modified.txt")
        root = Path(self.tmp.name) / "exports"
        root.mkdir()
        external = Path(self.tmp.name) / "external"
        external.mkdir()
        intermediate = root / f"{snapshot['target_sha']}-{item['id']}"
        intermediate.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(SnapshotError, "symlink"):
            export_source(snapshot, item, root)
        self.assertEqual(list(external.iterdir()), [])

    def test_export_diff_handles_deleted_and_renamed_files(self):
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        deleted = next(item for item in snapshot["files"] if item["status"] == "D")
        diff = export_diff(snapshot, deleted, Path(self.tmp.name) / "exports")
        self.assertEqual(Path(diff["left_path"]).read_bytes(), b"delete me\n")
        self.assertEqual(Path(diff["right_path"]).read_bytes(), b"")
        renamed = next(item for item in snapshot["files"] if item["status"] == "R")
        diff = export_diff(snapshot, renamed, Path(self.tmp.name) / "exports")
        self.assertEqual(Path(diff["left_path"]).read_bytes(), b"rename me\n")
        self.assertEqual(Path(diff["right_path"]).read_bytes(), b"rename me\n")
        self.assertEqual(shlex.split(diff["command"])[-2:], [diff["left_path"], diff["right_path"]])

    def test_errors_for_absent_side_invalid_line_and_traversal(self):
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        added = next(item for item in snapshot["files"] if item["status"] == "A")
        with self.assertRaisesRegex(SnapshotError, "source is absent"):
            read_blob(snapshot, added, side="base")
        with self.assertRaisesRegex(SnapshotError, "outside source range"):
            export_source(snapshot, added, Path(self.tmp.name) / "exports", line=99)
        malicious = dict(added, path="../escape")
        with self.assertRaisesRegex(SnapshotError, "traversal"):
            read_blob(snapshot, malicious)

    def test_inferred_main_ref_and_invalid_ref(self):
        snapshot = collect_snapshot(str(self.repo), target_ref="feature")
        self.assertEqual(snapshot["main_ref"], "main")
        with self.assertRaisesRegex(SnapshotError, "non-empty ref"):
            collect_snapshot(str(self.repo), main_ref="-bad", target_ref="feature")

    def test_new_resource_changes_are_filtered_but_asset_directory_code_remains(self):
        self.write('assets/logo.svg', '<svg/>\n')
        self.write('assets/font.woff2', 'font bytes\n')
        self.write('assets/config.json', '{"enabled":true}\n')
        self.git('add', '.')
        self.commit('asset and configuration changes')
        snapshot = collect_snapshot(str(self.repo), main_ref='main', target_ref='feature')
        names = {item['path'] for item in snapshot['files']}
        self.assertNotIn('assets/logo.svg', names)
        self.assertNotIn('assets/font.woff2', names)
        self.assertIn('assets/config.json', names)
        self.assertEqual(snapshot['excluded_resource_count'], 2)
        self.assertEqual(sum(snapshot['status_counts'].values()), len(snapshot['files']))

    def test_git_pathspec_syntax_in_a_filename_is_literal(self):
        self.write(":(glob)note.py", "literal name content\n")
        self.write("app/[id]/page.tsx", "dynamic route content\n")
        self.git("add", ".")
        self.commit("special names")
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        for name, expected in ((":(glob)note.py", "literal name content\n"), ("app/[id]/page.tsx", "dynamic route content\n")):
            item = next(file for file in snapshot["files"] if file["path"] == name)
            exported = export_source(snapshot, item, Path(self.tmp.name) / "exports")
            self.assertEqual(Path(exported["path"]).read_text(), expected)

    def test_submodule_tree_entry_is_rejected(self):
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.base},vendor/submodule")
        self.commit("submodule entry")
        snapshot = collect_snapshot(str(self.repo), main_ref="main", target_ref="feature")
        item = next(item for item in snapshot["files"] if item["path"] == "vendor/submodule")
        with self.assertRaisesRegex(SnapshotError, "submodule"):
            read_blob(snapshot, item)

def stat_mode(path):
    return path.stat().st_mode


if __name__ == "__main__":
    unittest.main()
