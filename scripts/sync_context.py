"""Stage a hash-verified public snapshot and its lock before installing either.

The synchronizer runs offline in the build checkout. Runtime deployments load an
immutable copy; this command is not a hot-reload protocol for a running server.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest_bytes(blob):
    return hashlib.sha256(blob).hexdigest()


def public_file(source, relative):
    path = Path(relative)
    if (path.is_absolute() or '..' in path.parts or str(path) != relative
            or (relative != '.gitignore' and any(part.startswith('.') for part in path.parts))):
        raise ValueError('Non-public path in release manifest')
    target = source / path
    target.resolve().relative_to(source)
    for file in (target, *list(target.parents)[:len(path.parts) - 1]):
        if file.is_symlink():
            raise ValueError('Symlink in public release')
    if not target.is_file():
        raise ValueError('Public release file missing')
    return target


def source_commit(source, manifest_sha256):
    try:
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True).strip()
        committed = subprocess.check_output(['git', '-C', str(source), 'show', revision + ':release-manifest.json'], stderr=subprocess.DEVNULL)
        if re.fullmatch(r'[a-f0-9]{40}', revision) and digest_bytes(committed) == manifest_sha256:
            return revision
    except (OSError, subprocess.CalledProcessError):
        pass
    return None


def install_snapshot(candidate, candidate_lock, destination, lock_path):
    """Keep the previous build snapshot intact on a caught installation failure."""
    previous, previous_lock = candidate.parent / 'previous-context', candidate.parent / 'previous-lock.json'
    installed = []
    try:
        if destination.exists():
            destination.rename(previous)
        if lock_path.exists():
            lock_path.rename(previous_lock)
        candidate.rename(destination)
        installed.append(destination)
        candidate_lock.rename(lock_path)
        installed.append(lock_path)
    except BaseException:
        for item in installed:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        if previous.exists():
            previous.rename(destination)
        if previous_lock.exists():
            previous_lock.rename(lock_path)
        raise


def sync(source):
    source = source.expanduser().resolve()
    manifest_path = public_file(source, 'release-manifest.json')
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = digest_bytes(manifest_bytes)
    manifest = json.loads(manifest_bytes)
    if (manifest.get('schema_version') != 3 or manifest.get('repository') != 'sunyuzheng/lizheng-open-context'
            or manifest.get('intended_visibility') != 'public'):
        raise ValueError('Expected the explicitly public lizheng-open-context release')
    files = manifest['files']
    if (not isinstance(files, list) or not files or len(files) > 10000
            or any(not isinstance(row, dict) or not isinstance(row.get('path'), str)
                   or not re.fullmatch(r'[a-f0-9]{64}', str(row.get('sha256', '')))
                   or type(row.get('bytes')) is not int or row['bytes'] < 0 for row in files)
            or len({row['path'] for row in files}) != len(files)
            or any(row['path'] == 'release-manifest.json' for row in files)):
        raise ValueError('Invalid public release file list')
    lock = {'repository': 'https://github.com/sunyuzheng/lizheng-open-context',
            'snapshot_at': manifest['snapshot_at'], 'release_manifest_sha256': manifest_sha256,
            'files': len(files), 'counts': manifest['counts'],
            'licenses': {name: info['files'] for name, info in manifest['licenses'].items()}}
    revision = source_commit(source, manifest_sha256)
    if not revision:
        raise ValueError('Public release manifest must match an immutable source commit')
    lock['source_commit'] = revision
    destination = ROOT / 'data' / 'context'
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        candidate = Path(temp) / 'context'
        candidate.mkdir()
        for row in files:
            # Hash and copy the same bytes, even if the source checkout changes.
            blob = public_file(source, row['path']).read_bytes()
            if digest_bytes(blob) != row['sha256'] or len(blob) != row['bytes']:
                raise ValueError('Release hash mismatch: ' + row['path'])
            target = candidate / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        (candidate / 'release-manifest.json').write_bytes(manifest_bytes)
        if not (candidate / 'scripts/search.py').is_file():
            raise ValueError('Search implementation missing from release')
        # Check the actual staged payload before replacing a usable snapshot.
        for row in files:
            blob = (candidate / row['path']).read_bytes()
            if digest_bytes(blob) != row['sha256'] or len(blob) != row['bytes']:
                raise ValueError('Staged release hash mismatch')
        if (candidate / 'release-manifest.json').read_bytes() != manifest_bytes:
            raise ValueError('Staged release manifest mismatch')
        candidate_lock = Path(temp) / 'context-lock.json'
        candidate_lock.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + '\n')
        install_snapshot(candidate, candidate_lock, destination, ROOT / 'data/context-lock.json')
    return lock


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    print(json.dumps(sync(parser.parse_args().source), ensure_ascii=False, indent=2))
