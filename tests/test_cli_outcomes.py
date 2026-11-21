"""Set EVAL_TEST_CLI to a freshly built CLI executable to test its exit codes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('EVAL_TEST_CLI'), 'Set EVAL_TEST_CLI to run real CLI tests')
class CliOutcomeTests(unittest.TestCase):
    def run_cli(self, filename, source, expected, output=os.devnull):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / filename
            if source is not None:
                path.write_text(source)
            result = subprocess.run(
                [os.environ['EVAL_TEST_CLI'], 'ast', str(path), '-o', output, '-c'],
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_accepted(self):
        self.run_cli('input.c', 'int f() { return 0; }', 0)

    def test_parser_and_lexer_rejections(self):
        for source in ('int f( {', 'int f() { return 1 }', 'int f() { return @; }'):
            with self.subTest(source=source):
                self.run_cli('input.c', source, 10)

    def test_builder_syntax_exception_is_not_parser_rejection(self):
        self.run_cli('input.lua', '', 11)

    def test_cast_is_supported_by_local_builder(self):
        self.run_cli('input.c', 'int f() { return (int) 1; }', 0)

    def test_sizeof_is_supported_by_local_builder(self):
        self.run_cli('input.c', 'int f() { int x; return sizeof(x); }', 0)

    def test_void_parameter_is_supported_by_local_builder(self):
        self.run_cli('input.c', 'static double pi(void) { return 3.14159; }', 0)

    def test_integer_beyond_int32_is_supported(self):
        self.run_cli('input.c', 'long f() { return 2147483648; }', 0)

    def test_comparison_uses_current_source_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = (Path(directory) / name for name in ('first.c', 'second.c'))
            first.write_text('int f() { return 1; }')
            for value, expected in ((1, 0), (2, 1)):
                second.write_text(f'int f() {{ return {value}; }}')
                result = subprocess.run(
                    [os.environ['EVAL_TEST_CLI'], 'cmp', str(first), str(second)],
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_unsupported_language(self):
        self.run_cli('input.unknown', 'anything', 12)

    def test_io_failures(self):
        self.run_cli('missing.c', None, 70)
        with tempfile.TemporaryDirectory() as directory:
            self.run_cli('input.c', 'int f() { return 0; }', 70, output=directory)


if __name__ == '__main__':
    unittest.main()
