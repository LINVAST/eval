"""Exercise pinned checkout selection and cache invalidation without a network."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from extractors import parser_cache


class ParserCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.origin = root / 'origin'
        self.origin.mkdir()
        self.git('init', '--quiet')
        (self.origin / 'src').mkdir()
        (self.origin / 'src/parser.c').write_text('pinned source')
        self.commit()
        self.pin = {'url': str(self.origin), 'revision': self.git('rev-parse', 'HEAD')}
        (self.origin / 'src/parser.c').write_text('new default branch source')
        self.commit()
        self.cache = root / 'cache'
        self.cache.mkdir()
        self.cfg = {'lang': 'c', 'grammar_dir': 'tree-sitter-c'}
        self.builds = 0
        self.version = '1'
        original_run = subprocess.run
        def run(args, **kwargs):
            if args[:2] == ['fake-tree-sitter', 'build']:
                self.builds += 1
                Path(args[3]).write_bytes(b'compiled parser')
                return subprocess.CompletedProcess(args, 0, b'', b'')
            return original_run(args, **kwargs)
        self.enterContext(patch.object(parser_cache, 'grammar_pin', return_value=self.pin))
        self.enterContext(patch.object(parser_cache, 'tool_identity', side_effect=lambda command: {
            'path': command, 'version': self.version, 'sha256': 'tool-hash'}))
        self.enterContext(patch.object(parser_cache.subprocess, 'run', side_effect=run))
        self.enterContext(patch.dict(os.environ))
        os.environ.pop(parser_cache.MANIFEST_ENV, None)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.origin), *args], stderr=subprocess.PIPE, text=True).strip()

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Cache Test', '-c', 'user.email=cache@example.invalid',
                 'commit', '--quiet', '-m', 'fixture')

    def prepare(self):
        return Path(parser_cache.ensure_parser(self.cfg, self.cache, 'fake-tree-sitter'))

    def test_pinned_revision_not_latest_and_valid_cache_reused(self):
        self.prepare()
        self.assertEqual((self.cache / 'tree-sitter-c/src/parser.c').read_text(), 'pinned source')
        self.prepare()
        self.assertEqual(self.builds, 1)

    def test_toolchain_change_invalidates_cache(self):
        self.prepare()
        self.version = '2'
        self.prepare()
        self.assertEqual(self.builds, 2)

    def test_corrupt_library_or_missing_metadata_invalidates_cache(self):
        library = self.prepare()
        library.write_bytes(b'corrupted')
        self.prepare()
        self.assertEqual(self.builds, 2)
        (self.cache / 'c.parser.json').unlink()
        self.prepare()
        self.assertEqual(self.builds, 3)

    def test_grammar_changes_are_not_silently_overwritten(self):
        self.prepare()
        source = self.cache / 'tree-sitter-c/src/parser.c'
        source.write_text('local edit')
        with self.assertRaisesRegex(ValueError, 'local changes'):
            self.prepare()
        self.assertEqual(source.read_text(), 'local edit')

    def test_invalid_metadata_and_changed_build_flags_rebuild(self):
        self.prepare()
        (self.cache / 'c.parser.json').write_text('null')
        self.prepare()
        self.assertEqual(self.builds, 2)
        os.environ['CFLAGS'] = '-O0 -g'
        self.prepare()
        self.assertEqual(self.builds, 3)

    def test_run_manifest_rejects_library_replaced_mid_run(self):
        library = self.prepare()
        metadata = json.loads((self.cache / 'c.parser.json').read_text())
        manifest = self.cache / 'run.json'
        manifest.write_text(json.dumps({'c': metadata}))
        os.environ[parser_cache.MANIFEST_ENV] = str(manifest)
        self.prepare()
        self.assertEqual(self.builds, 1)
        library.write_bytes(b'replaced')
        with self.assertRaisesRegex(ValueError, 'changed after run preparation'):
            self.prepare()


if __name__ == '__main__':
    unittest.main()
