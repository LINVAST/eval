"""Run with: python3 -m unittest discover -s extractors -p 'test_*.py'."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "extract_functions", Path(__file__).with_name("extract-functions.py")
)
extractor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extractor)


class ExtractionTests(unittest.TestCase):
    def extract(self, source, positions):
        source = source.encode("utf-8")
        return extractor.extract_text(
            source, extractor.line_offsets(source), *positions
        )

    def test_reported_utf8_prefix(self):
        self.assertEqual(
            self.extract("/*é*/ int f() {}\n", (0, 7, 0, 17)), "int f() {}"
        )

    def test_multibyte_identifier_and_body_exclude_suffix(self):
        self.assertEqual(
            self.extract('def café(): return "🌳"; extra\n', (0, 0, 0, 26)),
            'def café(): return "🌳"',
        )

    def test_multiline_nonzero_columns(self):
        self.assertEqual(
            self.extract('/*é*/ int f() {\n  /*界🌳*/ } suffix\n', (0, 7, 1, 15)),
            'int f() {\n  /*界🌳*/ }',
        )

    def test_unicode_on_previous_line(self):
        self.assertEqual(
            self.extract('// é界🌳\nint f() {}\n', (1, 0, 1, 10)), 'int f() {}'
        )

    def test_crlf_and_lone_cr_are_preserved(self):
        self.assertEqual(
            self.extract('/*é*/\r\nint f() {\r\n/*\r*/ }\r\n', (1, 0, 2, 7)),
            'int f() {\r\n/*\r*/ }',
        )

    def test_ascii_and_end_at_next_line(self):
        self.assertEqual(self.extract('int f() {}\n', (0, 0, 1, 0)), 'int f() {}\n')
        self.assertEqual(self.extract('int f() {}', (0, 0, 0, 10)), 'int f() {}')

    def test_materialized_name_code_wrapper_and_lines(self):
        source = '/*é*/ int café() {} // suffix\n'.encode('utf-8')
        output = '''pattern: 0
  capture: 1 - fn, start: (0, 7), end: (0, 21)
  capture: 0 - name, start: (0, 11), end: (0, 16)
'''
        self.assertEqual(
            extractor.parse_query_output(output, source, extractor.LANGUAGES['c']),
            [{"name": "café", "type": "function", "startLine": 1, "endLine": 1,
              "code": "int café() {}", "wrapped": "int café() {}\n"}],
        )

    def test_main_reads_original_bytes(self):
        output = '''pattern: 0
  capture: 1 - fn, start: (1, 7), end: (3, 1)
  capture: 0 - name, start: (1, 11), end: (1, 12)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.c'
            path.write_bytes('/* header */\r\n/*é*/ int f() {\r\n/*🌳*/\r\n}\r\n'.encode('utf-8'))
            stdout = io.StringIO()
            with patch.object(extractor.sys, 'argv', ['extract-functions.py', 'c', str(path)]), \
                    patch.object(extractor, 'ensure_parser', return_value='c.so'), \
                    patch.object(extractor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')), \
                    contextlib.redirect_stdout(stdout):
                self.assertEqual(extractor.main(), 0)
            unit = json.loads(stdout.getvalue())
            self.assertEqual(unit['code'], 'int f() {\r\n/*🌳*/\r\n}')
            self.assertEqual(unit['name'], 'f')
            self.assertEqual((unit['startLine'], unit['endLine']), (2, 4))


if __name__ == '__main__':
    unittest.main()
