"""Read and export immutable, committed Git snapshots.

The functions in this module deliberately operate on commits and trees.  They
never inspect the worktree for source contents and never change the checkout.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import tempfile
from typing import Any
from urllib.parse import quote

if __package__:
    from .file_policy import is_hidden_resource
else:
    from file_policy import is_hidden_resource


class SnapshotError(ValueError):
    """A requested snapshot operation cannot be completed safely."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
_STATUSES = ("A", "M", "D", "R", "C", "T")
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_GIT_TIMEOUT = 15.0


def _error(code: str, message: str) -> SnapshotError:
    return SnapshotError(code, message)


def _run_git(repo: str | Path, *args: str, timeout: float = _GIT_TIMEOUT) -> bytes:
    argv = [os.environ.get("REVIEW_COMPANION_GIT", "git"), *args]
    try:
        result = subprocess.run(
            argv,
            cwd=os.fspath(repo),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise _error("git_unavailable", "git executable is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise _error("timeout", "git command timed out") from exc
    except OSError as exc:
        raise _error("git_error", f"could not run git: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        if len(detail) > 400:
            detail = detail[:400] + "..."
        raise _error("git_error", detail or f"git exited with status {result.returncode}")
    return result.stdout


def _git_root(repository_path: str | Path) -> str:
    candidate = os.path.abspath(os.fspath(repository_path))
    if not os.path.isdir(candidate):
        raise _error("repository_not_found", f"repository path is not a directory: {candidate}")
    try:
        root = _run_git(candidate, "rev-parse", "--show-toplevel").decode("utf-8", "strict").strip()
    except UnicodeDecodeError as exc:
        raise _error("git_error", "git root is not valid UTF-8") from exc
    if not root:
        raise _error("not_repository", f"not a Git repository: {candidate}")
    return os.path.abspath(root)


def _valid_ref(ref: Any, name: str) -> str:
    if not isinstance(ref, str) or not ref or ref.startswith("-") or "\x00" in ref:
        raise _error("invalid_ref", f"{name} must be a non-empty ref name")
    return ref


def _resolve_commit(repo: str, ref: str, name: str = "ref") -> str:
    ref = _valid_ref(ref, name)
    try:
        raw = _run_git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    except SnapshotError as exc:
        if exc.code in ("timeout", "git_unavailable"):
            raise
        raise _error("ref_not_found", f"cannot resolve {name}: {ref}") from exc
    sha = raw.decode("ascii", "strict").strip().lower()
    if not _SHA_RE.fullmatch(sha):
        raise _error("git_error", f"git returned an invalid commit for {name}")
    return sha


def read_commit_subject(repository: str, commit_sha: str) -> str:
    """Resolve display text from a fixed commit, never from a moving ref."""
    if not isinstance(repository, str) or not repository or not isinstance(commit_sha, str) or not _SHA_RE.fullmatch(commit_sha):
        raise _error("invalid_snapshot", "a repository and fixed commit SHA are required")
    return _cached_commit_subject(str(Path(repository).expanduser().resolve()), commit_sha.lower())


@lru_cache(maxsize=256)
def _cached_commit_subject(repository: str, commit_sha: str) -> str:
    result = _run_git(repository, "--no-pager", "log", "-1", "--no-walk", "--no-show-signature", "--no-color", "--format=%s", "--encoding=UTF-8", f"{commit_sha}^{{commit}}", "--", timeout=1.0)
    return result.decode("utf-8", "replace").strip()[:240]


def _select_main_ref(repo: str) -> str:
    # The remote HEAD is a symbolic ref and therefore avoids assuming that a
    # repository's default branch is named main.
    try:
        remote_head = _run_git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").decode().strip()
    except SnapshotError:
        remote_head = ""
    if remote_head:
        try:
            _resolve_commit(repo, remote_head, "main_ref")
            return remote_head
        except SnapshotError:
            pass
    for candidate in ("main", "master"):
        try:
            _resolve_commit(repo, candidate, "main_ref")
            return candidate
        except SnapshotError:
            continue
    raise _error("main_ref_required", "could not infer main_ref; provide an explicit ref")


def _target_branch(repo: str, target_ref: str) -> str | None:
    """Return the local branch represented by a target ref, when it has one."""

    if target_ref == "HEAD":
        try:
            return _run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD").decode().strip() or None
        except SnapshotError:
            return None
    if target_ref.startswith("refs/heads/"):
        symbolic = target_ref
    else:
        symbolic = f"refs/heads/{target_ref}"
    try:
        _run_git(repo, "show-ref", "--verify", "--quiet", symbolic)
    except SnapshotError:
        return None
    return symbolic.removeprefix("refs/heads/")


def _canonical_path(raw: str | bytes, field: str = "path") -> str:
    if isinstance(raw, bytes):
        try:
            value = raw.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise _error("invalid_path", f"non-UTF-8 Git path in {field}") from exc
    elif isinstance(raw, str):
        value = raw
    else:
        raise _error("invalid_path", f"{field} must be a path")
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise _error("invalid_path", f"non-UTF-8 Git path in {field}") from exc
    if not value or "\x00" in value or value.startswith("/"):
        raise _error("invalid_path", f"invalid Git path in {field}")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise _error("invalid_path", f"path traversal in {field}")
    return "/".join(parts)


def _file_id(status: str, path: str, old_path: str | None) -> str:
    material = "\0".join((status, path, old_path or "")).encode("utf-8", "surrogateescape")
    return hashlib.sha256(material).hexdigest()


def _parse_name_status(data: bytes) -> list[dict[str, Any]]:
    fields = data.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        token = fields[index].decode("ascii", "strict")
        if not token:
            raise _error("git_error", "empty Git status token")
        status = token[0]
        if status not in _STATUSES:
            raise _error("git_error", f"unsupported Git status: {token}")
        if status in ("R", "C"):
            if index + 2 >= len(fields):
                raise _error("git_error", "incomplete rename status")
            old_path = _canonical_path(fields[index + 1], "old_path")
            new_path = _canonical_path(fields[index + 2], "newpath")
            index += 3
        else:
            if index + 1 >= len(fields):
                raise _error("git_error", "incomplete name status")
            old_path = None
            new_path = _canonical_path(fields[index + 1], "path")
            index += 2
        item: dict[str, Any] = {
            "id": _file_id(status, new_path, old_path),
            "path": new_path,
            "newpath": new_path if status in ("R", "C") else None,
            "old_path": old_path,
            "status": status,
        }
        result.append(item)
    return result


def collect_snapshot(
    repository_path: str, main_ref: str | None = None, target_ref: str = "HEAD"
) -> dict[str, Any]:
    """Collect a fixed, committed-only comparison between two refs."""

    repo = _git_root(repository_path)
    if main_ref is None:
        main_ref = _select_main_ref(repo)
    else:
        _valid_ref(main_ref, "main_ref")
    target_ref = _valid_ref(target_ref, "target_ref")
    main_sha = _resolve_commit(repo, main_ref, "main_ref")
    target_sha = _resolve_commit(repo, target_ref, "target_ref")
    try:
        base_sha = _run_git(repo, "merge-base", "--", main_sha, target_sha).decode("ascii", "strict").strip().lower()
    except (SnapshotError, UnicodeDecodeError) as exc:
        raise _error("merge_base_failed", "could not determine merge base") from exc
    if not _SHA_RE.fullmatch(base_sha):
        raise _error("merge_base_failed", "git returned an invalid merge base")
    diff = _run_git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--ignore-submodules=none",
        "--name-status",
        "-z",
        "--find-renames",
        "--find-copies",
        base_sha,
        target_sha,
        "--",
    )
    all_files = _parse_name_status(diff)
    files = [item for item in all_files if not is_hidden_resource(item)]
    status_counts = {status: 0 for status in _STATUSES}
    for item in files:
        status_counts[item["status"]] += 1
    target_branch = _target_branch(repo, target_ref)
    return {
        "repository": repo,
        "main_ref": main_ref,
        "main_sha": main_sha,
        "base_sha": base_sha,
        "target_ref": target_ref,
        "target_sha": target_sha,
        "target_branch": target_branch,
        "files": files,
        "status_counts": status_counts,
        "excluded_resource_count": len(all_files) - len(files),
    }


def _snapshot_sha(snapshot: dict[str, Any], side: str) -> str:
    key = "base_sha" if side == "base" else "target_sha"
    value = snapshot.get(key)
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise _error("invalid_snapshot", f"snapshot has no valid {key}")
    return value.lower()


def _source_path(file: dict[str, Any], side: str) -> str | None:
    status = file.get("status")
    if status not in _STATUSES:
        raise _error("invalid_file", "file has an unsupported status")
    if side == "base":
        if status == "A":
            return None
        value = file.get("old_path") if status in ("R", "C") else file.get("path")
    else:
        if status == "D":
            return None
        value = file.get("newpath") if status in ("R", "C") else file.get("path")
    if value is None:
        return None
    return _canonical_path(value)


def _tree_entry(repo: str, commit: str, path: str) -> tuple[str, str, str]:
    output = _run_git(repo, "--literal-pathspecs", "ls-tree", "-z", commit, "--", path)
    entries = output.split(b"\0")
    entries = [entry for entry in entries if entry]
    wanted = path.encode("utf-8", "surrogateescape")
    found: tuple[str, str, str] | None = None
    for entry in entries:
        try:
            metadata, name = entry.split(b"\t", 1)
            mode, kind, blob_sha = metadata.split(b" ", 2)
        except ValueError as exc:
            raise _error("git_error", "malformed tree entry") from exc
        if name == wanted:
            found = (mode.decode("ascii"), kind.decode("ascii"), blob_sha.decode("ascii"))
            break
    if found is None:
        raise _error("source_absent", f"path is absent from {commit}")
    mode, kind, blob_sha = found
    if mode == "160000" or kind == "commit":
        raise _error("submodule", f"submodule cannot be exported: {path}")
    if kind != "blob" or mode not in ("100644", "100755", "120000"):
        raise _error("unsupported_entry", f"unsupported tree entry: {path}")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", blob_sha):
        raise _error("git_error", "tree entry has an invalid blob SHA")
    return mode, kind, blob_sha.lower()


def read_blob(
    snapshot: dict[str, Any],
    file: dict[str, Any],
    side: str = "auto",
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> tuple[bytes, str, str]:
    """Read a blob from the immutable tree named by a snapshot."""

    if side not in ("auto", "base", "target"):
        raise _error("invalid_side", "side must be auto, base, or target")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise _error("invalid_limit", "max_bytes must be a positive integer")
    chosen = "base" if side == "auto" and file.get("status") == "D" else "target" if side == "auto" else side
    path = _source_path(file, chosen)
    if path is None:
        raise _error("source_absent", f"{chosen} source is absent for this file")
    repo = snapshot.get("repository")
    if not isinstance(repo, str):
        raise _error("invalid_snapshot", "snapshot has no repository")
    commit = _snapshot_sha(snapshot, chosen)
    # Verify that the supplied SHA names a commit, rather than a mutable ref.
    resolved = _resolve_commit(repo, commit, f"{chosen}_sha")
    if resolved != commit:
        raise _error("invalid_snapshot", f"snapshot {chosen} SHA does not resolve immutably")
    _mode, _kind, blob_sha = _tree_entry(repo, commit, path)
    try:
        size_raw = _run_git(repo, "cat-file", "-s", blob_sha).decode("ascii", "strict").strip()
        size = int(size_raw)
    except (SnapshotError, UnicodeDecodeError, ValueError) as exc:
        raise _error("git_error", "could not inspect blob size") from exc
    if size > max_bytes:
        raise _error("too_large", f"blob exceeds max_bytes ({size} > {max_bytes})")
    data = _run_git(repo, "cat-file", "blob", blob_sha)
    if len(data) != size:
        raise _error("git_error", "blob size changed while reading")
    return data, commit, path


def _text_bytes(data: bytes) -> str:
    if b"\x00" in data:
        raise _error("binary", "binary source cannot be opened at a line")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error("binary", "source is not UTF-8 text") from exc


def _line_check(data: bytes, line: int) -> None:
    if not isinstance(line, int) or isinstance(line, bool) or line < 1:
        raise _error("invalid_line", "line must be a positive integer")
    _text_bytes(data)
    max_line = data.count(b"\n") + 1
    if line > max_line:
        raise _error("invalid_line", f"line {line} is outside source range 1..{max_line}")


def _safe_component(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value or "source"


def _ensure_private_dir(path: Path) -> Path:
    existed = path.exists() or path.is_symlink()
    if path.is_symlink():
        raise _error("unsafe_export_path", f"export directory is a symlink: {path}")
    if not existed:
        try:
            path.mkdir(mode=0o700)
        except FileNotFoundError:
            # Only the caller-supplied export root may need its missing parent
            # chain. Controlled children are created after their parent has
            # been checked and therefore never use this branch.
            path.mkdir(mode=0o700, parents=True)
    if path.is_symlink() or not path.is_dir():
        raise _error("unsafe_export_path", f"export path is not a private directory: {path}")
    if not existed:
        try:
            os.chmod(path, 0o700)
        except OSError as exc:
            raise _error("export_error", f"could not protect export directory: {exc}") from exc
    return path.resolve()


def _write_readonly(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise _error("export_error", f"could not inspect export file: {exc}") from exc
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise _error("unsafe_export_path", f"export target is not a regular file: {path}")
        try:
            if path.read_bytes() == data:
                os.chmod(path, 0o444)
                return
        except OSError as exc:
            raise _error("export_error", f"could not inspect existing export: {exc}") from exc
    parent = path.parent
    try:
        with tempfile.NamedTemporaryFile(dir=parent, prefix=".tmp-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, path)
        os.chmod(path, 0o444)
    except OSError as exc:
        try:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise _error("export_error", f"could not write export: {exc}") from exc


def _export_dir(export_root: Path, commit: str, file_id: str, side: str | None = None) -> Path:
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", commit):
        raise _error("invalid_snapshot", "invalid commit SHA")
    if not isinstance(file_id, str) or not re.fullmatch(r"[0-9a-fA-F]{16,64}", file_id):
        raise _error("invalid_file", "invalid file id")
    root = _ensure_private_dir(export_root)
    directory = _ensure_private_dir(root / f"{commit.lower()}-{file_id.lower()}")
    if side:
        directory = directory / _safe_component(side)
        directory = _ensure_private_dir(directory)
    return directory


def _export_one(
    snapshot: dict[str, Any],
    file: dict[str, Any],
    export_root: Path,
    side: str,
    line: int | None,
    allow_absent: bool = False,
) -> tuple[str | None, str | None, str | None]:
    path = _source_path(file, side)
    if path is None:
        if not allow_absent:
            raise _error("source_absent", f"{side} source is absent for this file")
        commit = _snapshot_sha(snapshot, side)
        directory = _export_dir(export_root, commit, str(file.get("id", "")), side)
        empty = directory / "empty"
        _write_readonly(empty, b"")
        return str(empty.resolve()), commit, None
    data, commit, original_path = read_blob(snapshot, file, side)
    if line is not None:
        _line_check(data, line)
    else:
        _text_bytes(data)
    directory = _export_dir(export_root, commit, str(file.get("id", "")), side)
    basename = _safe_component(PurePosixPath(original_path).name)
    destination = directory / basename
    _write_readonly(destination, data)
    return str(destination.resolve()), commit, original_path


def export_source(
    snapshot: dict[str, Any],
    file: dict[str, Any],
    export_root: Path,
    line: int = 1,
    side: str = "auto",
) -> dict[str, Any]:
    """Export a committed text source file and return editor coordinates."""

    if side not in ("auto", "base", "target"):
        raise _error("invalid_side", "side must be auto, base, or target")
    chosen = "base" if side == "auto" and file.get("status") == "D" else "target" if side == "auto" else side
    destination, commit, original_path = _export_one(snapshot, file, Path(export_root), chosen, line)
    assert destination is not None and commit is not None and original_path is not None
    absolute = os.path.abspath(destination)
    uri = "vscode://file" + quote(absolute, safe="/") + f":{line}:1"
    command = shlex.join(["code", "--reuse-window", "--goto", f"{absolute}:{line}:1"])
    return {
        "path": absolute,
        "line": line,
        "commit_sha": commit,
        "original_path": original_path,
        "vscode_uri": uri,
        "command": command,
    }


def export_diff(snapshot: dict[str, Any], file: dict[str, Any], export_root: Path) -> dict[str, Any]:
    """Export both fixed versions of a changed file for ``code --diff``."""

    root = Path(export_root)
    left, base_sha, _ = _export_one(snapshot, file, root, "base", None, allow_absent=True)
    right, target_sha, _ = _export_one(snapshot, file, root, "target", None, allow_absent=True)
    assert left is not None and right is not None and base_sha is not None and target_sha is not None
    command = shlex.join(["code", "--reuse-window", "--diff", left, right])
    return {
        "left_path": os.path.abspath(left),
        "right_path": os.path.abspath(right),
        "base_sha": base_sha,
        "target_sha": target_sha,
        "command": command,
    }


__all__ = ["SnapshotError", "collect_snapshot", "read_commit_subject", "read_blob", "export_source", "export_diff"]
