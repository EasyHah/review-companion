"""Check or publish exactly release-files.json; never enumerate runtime artifacts."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILE_LIST = 'release-files.json'
MANIFESTS = ('package.json', 'plugin.json', '.codex-plugin/plugin.json', '.claude-plugin/plugin.json')
EXECUTABLES = ('bin/mcp-launch', 'bin/session-hook', 'bin/review-pi.mjs')


class ReleaseError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(f'{code}: {detail}')


def read_release(root):
    try:
        if (root / FILE_LIST).is_symlink(): raise ReleaseError('release_symlink', FILE_LIST)
        manifest_bytes = (root / FILE_LIST).read_bytes()
        manifest = json.loads(manifest_bytes)
    except (OSError, ValueError) as error:
        if isinstance(error, ReleaseError): raise
        raise ReleaseError('invalid_release_manifest', FILE_LIST + ' is missing or invalid JSON') from error
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'files'} or manifest['schema_version'] != 1:
        raise ReleaseError('invalid_release_manifest', 'expected schema_version=1 and files')
    names = manifest['files']
    if not isinstance(names, list) or not names or len(names) > 2000:
        raise ReleaseError('invalid_release_manifest', 'files must be a nonempty bounded list')
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name or len(name) > 1000 or '\\' in name or any(ord(c) < 32 for c in name):
            raise ReleaseError('invalid_release_manifest', 'invalid file path')
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or name != path.as_posix() or name in seen:
            raise ReleaseError('invalid_release_manifest', 'unsafe or duplicate path: ' + name)
        seen.add(name)
    missing = set((FILE_LIST,) + MANIFESTS + EXECUTABLES) - seen
    if missing: raise ReleaseError('invalid_release_manifest', 'required entries missing: ' + ', '.join(sorted(missing)))
    files = {}
    for name in sorted(names):
        path = root
        for part in PurePosixPath(name).parts:
            path = path / part
            if path.is_symlink(): raise ReleaseError('release_symlink', name)
        if not path.is_file(): raise ReleaseError('release_file_missing', name)
        try: files[name] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        except OSError as error: raise ReleaseError('release_file_unreadable', name) from error
    if files[FILE_LIST][0] != manifest_bytes:
        raise ReleaseError('release_changed_during_read', FILE_LIST)
    for name in EXECUTABLES:
        if not files[name][1] & 0o111: raise ReleaseError('release_executable_missing', name)
    metadata = {}
    for name in MANIFESTS:
        try: value = json.loads(files[name][0])
        except ValueError as error: raise ReleaseError('release_metadata_invalid', name) from error
        if not isinstance(value, dict): raise ReleaseError('release_metadata_invalid', name)
        metadata[name] = value
    package = metadata['package.json']
    version = package.get('version', '')
    if package.get('name') != 'review-companion' or not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ReleaseError('release_metadata_invalid', 'package.json name/version')
    for name, value in metadata.items():
        if value.get('name') != package['name'] or value.get('version') != version:
            raise ReleaseError('release_metadata_mismatch', name + ' name/version differs from package.json')
    digest = hashlib.sha256()
    for name, (data, mode) in files.items():
        digest.update(name.encode()); digest.update(b'\0'); digest.update(str(mode).encode()); digest.update(b'\0'); digest.update(hashlib.sha256(data).digest())
    return version, files, digest.hexdigest()


def build(root, destination=None, check=False):
    root = root.resolve()
    version, files, source_digest = read_release(root)
    result = {'checked': True, 'version': version, 'files': len(files), 'source_digest': source_digest}
    if check: return result
    archive = Path(destination).expanduser().absolute() if destination else root.parent / f'review-companion-{version}.zip'
    checksum = archive.with_suffix(archive.suffix + '.sha256')
    protected = {(root / name).resolve() for name in files}
    if archive.resolve() in protected or checksum.resolve() in protected:
        raise ReleaseError('output_conflict', 'archive/checksum would overwrite a declared release file')
    archive.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.review-release-', suffix='.zip', dir=archive.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as output:
            for name, (data, mode) in files.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                output.writestr(info, data)
        with zipfile.ZipFile(temporary) as verified:
            if verified.testzip() is not None: raise ReleaseError('archive_invalid', 'ZIP integrity check failed')
        digest = hashlib.sha256(Path(temporary).read_bytes()).hexdigest()
        checksum_fd, checksum_tmp = tempfile.mkstemp(prefix='.review-checksum-', dir=archive.parent)
        try:
            with os.fdopen(checksum_fd, 'w') as stream:
                stream.write(digest + '  ' + archive.name + '\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, archive)
            os.replace(checksum_tmp, checksum)
        finally:
            if os.path.exists(checksum_tmp): os.unlink(checksum_tmp)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return {**result, 'archive': str(archive), 'bytes': archive.stat().st_size, 'sha256': digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='read-only validation; no plan, ZIP or configuration writes')
    parser.add_argument('--output', help='ZIP destination (not an installation plan)')
    args = parser.parse_args()
    try: print(json.dumps(build(ROOT, args.output, args.check)))
    except (ReleaseError, OSError) as error:
        print('review-release: ' + str(error), file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__': main()
