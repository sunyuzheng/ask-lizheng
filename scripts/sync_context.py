"""Copy only hash-verified public release files; never copy the source checkout."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def sync(source):
    source = source.expanduser().resolve()
    manifest_path = source / 'release-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('repository') != 'sunyuzheng/lizheng-open-context' or manifest.get('intended_visibility') != 'public':
        raise ValueError('Expected the explicitly public lizheng-open-context release')
    files = list(manifest['files'])
    for row in files:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or (str(relative) != '.gitignore' and any(part.startswith('.') for part in relative.parts)):
            raise ValueError('Non-public path in release manifest')
        target = (source / relative).resolve()
        target.relative_to(source)
        if digest(target) != row['sha256']:
            raise ValueError('Release hash mismatch: ' + str(relative))
    destination = ROOT / 'data' / 'context'
    destination.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        candidate = Path(temp) / 'context'
        candidate.mkdir()
        for row in files:
            target = candidate / row['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / row['path'], target)
        # License files are release files like any other; only the manifest describes itself.
        shutil.copyfile(manifest_path, candidate / 'release-manifest.json')
        if not (candidate / 'scripts/search.py').is_file():
            raise ValueError('Search implementation missing from release')
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(candidate), destination)
    lock = {'repository': 'https://github.com/sunyuzheng/lizheng-open-context',
            'snapshot_at': manifest['snapshot_at'], 'release_manifest_sha256': digest(manifest_path),
            'files': len(files), 'counts': manifest['counts'],
            'licenses': {name: info['files'] for name, info in manifest['licenses'].items()}}
    try:
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL, text=True).strip()
        committed = subprocess.check_output(['git', '-C', str(source), 'show', revision + ':release-manifest.json'], stderr=subprocess.DEVNULL)
        if re.fullmatch(r'[a-f0-9]{40}', revision) and hashlib.sha256(committed).hexdigest() == lock['release_manifest_sha256']:
            lock['source_commit'] = revision
    except (OSError, subprocess.CalledProcessError):
        pass
    (ROOT / 'data/context-lock.json').write_text(json.dumps(lock, ensure_ascii=False, indent=2) + '\n')
    return lock

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    print(json.dumps(sync(parser.parse_args().source), ensure_ascii=False, indent=2))
