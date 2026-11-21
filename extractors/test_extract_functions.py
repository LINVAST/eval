"""Run with: python3 -m unittest discover -s extractors -p 'test_*.py'."""

import contextlib
import ast
import importlib.util
import io
import json
from pathlib import Path
import shutil
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

    def test_declaration_tree_is_independent_of_query_order(self):
        outer = {'fn_pos': (0, 0, 4, 1)}
        nested = {'fn_pos': (1, 4, 3, 5)}
        same_start_child = {'fn_pos': (0, 0, 0, 8)}
        sibling = {'fn_pos': (4, 2, 4, 20)}
        matches = [nested, sibling, same_start_child, outer, dict(outer)]
        selected = extractor.declaration_matches(matches)
        self.assertEqual([m['fn_pos'] for m in selected],
                         [outer['fn_pos'], same_start_child['fn_pos'], nested['fn_pos'], sibling['fn_pos']])
        self.assertEqual(selected[0]['children'], selected[1:3])

    def test_same_line_adjacent_declarations_remain_separate(self):
        first = {'fn_pos': (0, 0, 0, 10)}
        second = {'fn_pos': (0, 10, 0, 20)}
        selected = extractor.declaration_matches([second, first])
        self.assertEqual([m['fn_pos'] for m in selected], [first['fn_pos'], second['fn_pos']])
        self.assertTrue(all(not m['children'] for m in selected))

    def test_repeated_range_keeps_first_captured_name(self):
        first = {'fn_pos': (0, 0, 3, 1), 'name_pos': (1, 1, 1, 2)}
        second = {'fn_pos': (0, 0, 3, 1), 'name_pos': (2, 1, 2, 2)}
        selected = extractor.declaration_matches([second, first])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['name_pos'], first['name_pos'])

    def test_crossing_ranges_fail_instead_of_silently_losing_source(self):
        with self.assertRaisesRegex(ValueError, 'overlap without containment'):
            extractor.declaration_matches([
                {'fn_pos': (0, 0, 0, 10)}, {'fn_pos': (0, 5, 0, 15)},
            ])

    @unittest.skipUnless(
        shutil.which(extractor.TS_BIN) and all(
            (Path(extractor.SELF_DIR) / (lang + '.so')).is_file()
            for lang in extractor.LANGUAGES
        ), 'Requires Tree-sitter CLI and the six cached parsers',
    )
    def test_real_cli_separates_children_from_parents_in_all_languages(self):
        cases = {
            'c': ('struct Outer { struct Inner {int x;} field; }; '
                  'int f(){ struct Local {int x;}; return 0; }', ['Outer', 'Inner', 'f', 'Local']),
            'go': ('package p\ntype (\n A int\n B string\n)\n'
                   'func f() { type Local int }\n', ['A', 'f', 'Local']),
            'java': ('class Outer { Outer() {} void f() {} '
                     'class Inner { void g() {} } } class Other {}', ['Outer', 'Outer', 'f', 'Inner', 'g', 'Other']),
            'python': ('class Outer:\n def f(self):\n  def inner(): pass\n  return inner\n'
                       'def top():\n def nested(): pass\n return nested\n', ['Outer', 'f', 'inner', 'top', 'nested']),
            'kotlin': ('class Outer {\n fun f() {}\n class Inner {\n fun g() {}\n }\n }\n'
                       'fun top() { fun nested() {} }\n', ['Outer', 'f', 'Inner', 'g', 'top', 'nested']),
            'lua': ('function outer() function inner() end end\n'
                    'function other() end\n', ['outer', 'inner', 'other']),
        }
        with tempfile.TemporaryDirectory() as directory:
            for lang, (source, names) in cases.items():
                with self.subTest(lang=lang):
                    path = Path(directory) / ('sample.' + {'python': 'py', 'kotlin': 'kt'}.get(lang, lang))
                    path.write_text(source)
                    result = subprocess.run(
                        [extractor.sys.executable, extractor.__file__, lang, str(path)],
                        capture_output=True, text=True, check=True,
                    )
                    units = [json.loads(line) for line in result.stdout.splitlines()]
                    self.assertEqual([unit['name'] for unit in units], names)
                    parent = units[0]['code']
                    if lang == 'c':
                        self.assertNotIn('int x', parent)
                        self.assertIn('struct Inner', parent)
                        self.assertIn('field;', parent)
                        self.assertIn('int x', units[1]['code'])
                    elif lang == 'go':
                        self.assertNotIn('type Local', units[1]['code'])
                        self.assertIn('type Local', units[2]['code'])
                    else:
                        child_declaration = {'java': 'void f', 'python': 'def f',
                                             'kotlin': 'fun f', 'lua': 'function inner'}[lang]
                        self.assertNotIn(child_declaration, parent)
                    if lang == 'python':
                        for unit in units:
                            ast.parse(unit['code'])

    def test_java_parent_keeps_members_but_not_nested_declarations(self):
        source = '/*é*/ class A { int x; void f() {} class B { int y; void g() {} } }\r\n'.encode('utf-8')
        def match(text, name, pattern):
            data, ident = text.encode('utf-8'), name.encode('utf-8')
            start = source.index(data)
            name_start = start + data.index(ident)
            return {'pat': pattern, 'fn_pos': (0, start, 0, start + len(data)),
                    'name_pos': (0, name_start, 0, name_start + len(ident))}
        outer = match('class A { int x; void f() {} class B { int y; void g() {} } }', 'A', 2)
        inner = match('class B { int y; void g() {} }', 'B', 2)
        f = match('void f() {}', 'f', 1)
        g = match('void g() {}', 'g', 1)
        units = [extractor._materialize(m, source, extractor.line_offsets(source), extractor.LANGUAGES['java'])
                 for m in extractor.declaration_matches([g, f, inner, outer])]
        self.assertEqual([u['name'] for u in units], ['A', 'f', 'B', 'g'])
        self.assertEqual(' '.join(units[0]['code'].split()), 'class A { int x; }')
        self.assertEqual(units[1]['code'], 'void f() {}')
        self.assertEqual(' '.join(units[2]['code'].split()), 'class B { int y; }')
        self.assertEqual(units[3]['code'], 'void g() {}')

    @unittest.skipUnless(shutil.which(extractor.TS_BIN) and (Path(extractor.SELF_DIR) / 'python.so').is_file(),
                         'Requires the cached Python parser')
    def test_python_decorators_and_empty_suites_remain_valid(self):
        source = ('class A:\r\n    @x\r\n    def café(self):\r\n'
                  '        if True:\r\n            @x\r\n            def inner():\r\n'
                  '                return "🌳"\r\n'
                  '    @x\r\n    class B:\r\n        def g(self): pass\r\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.py'
            path.write_bytes(source.encode('utf-8'))
            output = subprocess.check_output([extractor.sys.executable, extractor.__file__, 'python', str(path)], text=True)
        units = [json.loads(line) for line in output.splitlines()]
        self.assertEqual([u['name'] for u in units], ['A', 'café', 'inner', 'B', 'g'])
        for unit in units:
            ast.parse(unit['code'])
            self.assertNotIn('@x', unit['code'])
        self.assertNotIn('def café', units[0]['code'])
        self.assertNotIn('class B', units[0]['code'])
        self.assertNotIn('def inner', units[1]['code'])
        self.assertEqual(units[0]['code'].count('\r\n'), source.rstrip('\r\n').count('\r\n'))


if __name__ == '__main__':
    unittest.main()
