"""Content and environment identities shared by evaluation and parser caching."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def tool_identity(command, version_arg='--version'):
    path = shutil.which(command)
    if path is None:
        raise FileNotFoundError(f'Required tool not found: {command}')
    path = str(Path(path).resolve())
    version = subprocess.check_output([path, version_arg], stderr=subprocess.STDOUT, text=True).strip()
    return {'path': path, 'version': version, 'sha256': file_sha256(path)}


def repository_identity(path):
    """Record the commit plus hashes of tracked changes and untracked files."""
    def git(*args):
        return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.PIPE)
    try:
        root = Path(os.fsdecode(git('rev-parse', '--show-toplevel')).strip())
    except subprocess.CalledProcessError:
        return {'path': str(Path(path).resolve()), 'repository': False}
    path = root
    revision = git('rev-parse', 'HEAD').decode().strip()
    diff = git('diff', '--binary', '--ignore-submodules=all', 'HEAD', '--', '.')
    changed = git('diff', '--name-only', '-z', '--ignore-submodules=all', 'HEAD', '--', '.')
    untracked = git('ls-files', '--others', '--exclude-standard', '-z')
    files = []
    for name in sorted(set(changed.split(b'\0') + untracked.split(b'\0')) - {b''}):
        relative = os.fsdecode(name)
        full = root / relative
        files.append({'path': relative, 'sha256': file_sha256(full) if full.is_file() else None})
    return {'path': str(root), 'repository': True, 'commit': revision,
            'dirty': bool(diff or files), 'diff_sha256': hashlib.sha256(diff).hexdigest(),
            'changed_files': files}
