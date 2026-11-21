"""Build pinned Tree-sitter grammars and validate the native parser cache."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile

from provenance import file_sha256, tool_identity


LOCK_PATH = Path(__file__).with_name('grammars.lock.json')
MANIFEST_ENV = 'LINVAST_PARSER_MANIFEST'
BUILD_ENV = ('CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS', 'AR', 'CXXSTDLIB',
             'CRATE_CC_NO_DEFAULTS')


def grammar_pin(lang):
    lock = json.loads(LOCK_PATH.read_text())
    if lock.get('schema') != 1:
        raise ValueError('Unsupported grammar lock schema')
    pin = lock['grammars'][lang]
    if not re.fullmatch(r'[0-9a-f]{40}', pin['revision']):
        raise ValueError(f'Grammar {lang} must be pinned to a full commit ID')
    return pin


def git(grammar, *args):
    return subprocess.check_output(['git', '-C', str(grammar), *args], stderr=subprocess.PIPE).decode().strip()


def prepare_grammar(grammar, pin):
    if not grammar.exists():
        grammar.mkdir(parents=True)
        git(grammar, 'init', '--quiet')
    # Never discard user edits while changing a cached checkout.
    if git(grammar, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError(f'Grammar cache has local changes: {grammar}')
    available = subprocess.run(['git', '-C', str(grammar), 'cat-file', '-e', pin['revision'] + '^{commit}'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if available.returncode:
        git(grammar, 'fetch', '--depth', '1', pin['url'], pin['revision'])
    git(grammar, 'checkout', '--quiet', '--detach', pin['revision'])


def source_identity(grammar):
    # Hash every build input under src, including generated parsers and scanners.
    paths = list((grammar / 'src').rglob('*'))
    paths += [grammar / name for name in ('grammar.js', 'tree-sitter.json', 'package.json')]
    files = {str(path.relative_to(grammar)): file_sha256(path)
             for path in sorted(paths) if path.is_file()}
    if 'src/parser.c' not in files:
        raise ValueError(f'Pinned grammar has no generated src/parser.c: {grammar}')
    return {'files': files, 'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def cache_valid(metadata, identity, library):
    return (isinstance(metadata, dict) and metadata.get('identity') == identity and library.is_file()
            and metadata.get('library_sha256') == file_sha256(library))


def ensure_parser(cfg, cache_dir, ts_bin):
    cache_dir = Path(cache_dir)
    pin = grammar_pin(cfg['lang'])
    library = cache_dir / (cfg['lang'] + '.so')
    # Evaluation prepares and records parsers once. Each extraction then checks
    # the exact binary against that run's snapshot instead of rebuilding it.
    if os.environ.get(MANIFEST_ENV):
        metadata = json.loads(Path(os.environ[MANIFEST_ENV]).read_text())[cfg['lang']]
        if (metadata['identity']['grammar'] != pin
                or metadata['library_sha256'] != file_sha256(library)
                or metadata['library'] != str(library.resolve())):
            raise ValueError(f'Parser changed after run preparation: {cfg["lang"]}')
        return str(library)

    with (cache_dir / '.parser-cache.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        grammar = cache_dir / cfg['grammar_dir']
        prepare_grammar(grammar, pin)
        identity = {'schema': 1, 'grammar': pin, 'sources': source_identity(grammar),
                    'tree_sitter': tool_identity(ts_bin),
                    'cc': tool_identity(os.environ.get('CC', 'cc')),
                    'cxx': tool_identity(os.environ.get('CXX', 'c++')),
                    'platform': {'system': platform.system(), 'machine': platform.machine()},
                    'environment': {key: os.environ.get(key) for key in BUILD_ENV}}
        metadata_path = cache_dir / (cfg['lang'] + '.parser.json')
        try:
            metadata = json.loads(metadata_path.read_text())
        except (OSError, ValueError):
            metadata = {}
        if cache_valid(metadata, identity, library):
            return str(library)
        environment = {**os.environ, 'CC': identity['cc']['path'], 'CXX': identity['cxx']['path'],
                       'XDG_CACHE_HOME': str(cache_dir / '.build-cache')}
        with tempfile.TemporaryDirectory(dir=cache_dir, prefix='.parser-build-') as directory:
            temporary_library = Path(directory) / library.name
            subprocess.run([ts_bin, 'build', '-o', str(temporary_library)], cwd=grammar,
                           env=environment, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            metadata = {'identity': identity, 'library': str(library.resolve()),
                        'library_sha256': file_sha256(temporary_library)}
            temporary_metadata = Path(directory) / 'metadata.json'
            temporary_metadata.write_text(json.dumps(metadata, indent=2) + '\n')
            os.replace(temporary_library, library)
            os.replace(temporary_metadata, metadata_path)
        return str(library)
