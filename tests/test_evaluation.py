"""Exercise the runner with real child processes and controlled failure modes."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('evaluation', Path(__file__).resolve().parents[1] / 'evaluation.py')
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'lang/c/sample'
        self.project.mkdir(parents=True)
        self.extractor = self.root / 'extract.py'
        self.extractor.write_text('''import pathlib, sys
text = pathlib.Path(sys.argv[2]).read_text()
if text.startswith('FAIL'):
    print(text.split('\\n', 1)[1])
    print('query/build/decode failure', file=sys.stderr)
    sys.exit(2)
print(text, end='')
''')
        self.cli = self.root / 'cli'
        self.cli.write_text('''#!/bin/sh
case "$(cat "$2")" in
accepted) exit 0;;
parser) exit 10;;
builder) exit 11;;
builder_error) exit 13;;
unsupported) exit 12;;
timeout) sleep 10;;
infrastructure) exit 70;;
legacy) exit 1;;
signal) kill -TERM $$;;
esac
''')
        self.cli.chmod(0o755)
        self.output = self.root / 'results'

    def unit(self, result='accepted'):
        return json.dumps({'name': 'f', 'type': 'function', 'code': result,
                           'wrapped': result, 'startLine': 1, 'endLine': 1}) + '\n'

    def source(self, name, text):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_evaluation(self, targets=None, whole_files=False, build=False):
        runner = evaluation.Evaluation(self.root, self.cli, self.extractor,
                                       self.output, timeout=0.1, whole_files=whole_files)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = runner.run(targets or ['lang/c/sample'], build=build)
        summary = json.loads((self.output / 'summary.json').read_text())
        events = [json.loads(line) for line in (self.output / 'events.jsonl').read_text().splitlines()]
        return code, summary, events

    def test_separate_outcomes_exclusions_zero_matches_and_spaces(self):
        for result in ('accepted', 'parser', 'builder', 'unsupported', 'timeout'):
            self.source(result + '.c', self.unit(result))
        self.source('space in name.c', self.unit())
        self.source('empty.c', '')
        self.source('tests/excluded.c', self.unit())
        code, summary, events = self.run_evaluation()
        self.assertEqual(code, 0)
        self.assertEqual(summary['status'], 'complete')
        counts = summary['totals']
        self.assertEqual(counts['files_discovered'], 8)
        self.assertEqual(counts['files_excluded'], 1)
        self.assertEqual(counts['files_extracted'], 7)
        self.assertEqual(counts['zero_match_files'], 1)
        self.assertEqual(counts['matched_constructs'], 6)
        self.assertEqual(counts['units_attempted'], 6)
        self.assertEqual(counts['accepted_asts'], 2)
        for key in ('parser_rejections', 'builder_rejections', 'unsupported_language_rejections', 'timeouts'):
            self.assertEqual(counts[key], 1)
        self.assertEqual(counts['infrastructure_failures'], 0)
        self.assertEqual(sum(event['event'] == 'unit_result' for event in events), 6)

    def test_failed_extractor_partial_output_is_never_counted(self):
        self.source('a.c', 'FAIL\n' + self.unit())
        self.source('z.c', self.unit())
        code, summary, events = self.run_evaluation()
        self.assertEqual(code, 1)
        self.assertEqual(summary['status'], 'aborted')
        counts = summary['totals']
        self.assertEqual(counts['files_discovered'], 2)
        self.assertEqual(counts['extraction_failures'], 1)
        self.assertEqual(counts['infrastructure_failures'], 1)
        self.assertEqual(counts['zero_match_files'], 0)
        self.assertEqual(counts['matched_constructs'], 0)
        self.assertEqual(counts['units_attempted'], 0)
        self.assertTrue(any(event.get('exit_code') == 2 for event in events))
        self.assertIn('query/build/decode failure', (self.output / 'diagnostics.log').read_text())

    def test_malformed_or_incomplete_json_aborts_before_any_unit(self):
        for invalid in ('{', '{}', 'null', '{"wrapped": "x"}', '\ufffd'):
            with self.subTest(invalid=invalid):
                self.source('a.c', self.unit() + invalid)
                code, summary, _ = self.run_evaluation()
                self.assertEqual(code, 1)
                self.assertEqual(summary['totals']['extraction_failures'], 1)
                self.assertEqual(summary['totals']['units_attempted'], 0)

    def test_linvast_errors_do_not_stop_evaluation(self):
        for result, counter in (('builder_error', 'builder_errors'), ('legacy', 'cli_errors'), ('signal', 'cli_errors')):
            with self.subTest(result=result):
                self.source('a.c', self.unit(result))
                self.source('z.c', self.unit())
                code, summary, _ = self.run_evaluation()
                self.assertEqual(code, 0)
                self.assertEqual(summary['totals']['infrastructure_failures'], 0)
                self.assertEqual(summary['totals']['units_attempted'], 2)
                self.assertEqual(summary['totals'][counter], 1)
                self.assertEqual(summary['totals']['accepted_asts'], 1)
                self.assertEqual(summary['totals']['builder_rejections'], 0)
                self.assertEqual(summary['totals']['timeouts'], 0)

    def test_infrastructure_failure_aborts(self):
        self.source('a.c', self.unit('infrastructure'))
        self.source('z.c', self.unit())
        code, summary, _ = self.run_evaluation()
        self.assertEqual(code, 1)
        self.assertEqual(summary['totals']['infrastructure_failures'], 1)
        self.assertEqual(summary['totals']['units_attempted'], 1)

    def test_whole_file_mode_has_separate_submission_count(self):
        self.source('a.c', 'accepted')
        self.source('b.c', 'parser')
        code, summary, _ = self.run_evaluation(whole_files=True)
        self.assertEqual(code, 0)
        self.assertEqual(summary['totals']['whole_files_submitted'], 2)
        self.assertEqual(summary['totals']['matched_constructs'], 0)
        self.assertEqual(summary['totals']['accepted_asts'], 1)
        self.assertEqual(summary['totals']['parser_rejections'], 1)

    def test_overlapping_targets_do_not_double_count(self):
        self.source('a.c', self.unit())
        code, summary, _ = self.run_evaluation(['lang/c', 'lang/c/sample'])
        self.assertEqual(code, 0)
        self.assertEqual(summary['totals']['files_discovered'], 1)
        self.assertEqual(summary['totals']['matched_constructs'], 1)

    def test_discovery_failure_aborts(self):
        code, summary, _ = self.run_evaluation(['lang/c/missing'])
        self.assertEqual(code, 1)
        self.assertEqual(summary['totals']['infrastructure_failures'], 1)

    def test_failed_publish_does_not_use_stale_cli(self):
        self.source('a.c', self.unit())
        original_run = evaluation.subprocess.run

        def run(command, **kwargs):
            if command[0] == 'dotnet':
                return evaluation.subprocess.CompletedProcess(command, 1)
            return original_run(command, **kwargs)

        with patch.object(evaluation.subprocess, 'run', side_effect=run):
            code, summary, _ = self.run_evaluation(build=True)
        self.assertEqual(code, 1)
        self.assertEqual(summary['totals']['infrastructure_failures'], 1)
        self.assertEqual(summary['totals']['units_attempted'], 0)

    def test_missing_cli_aborts(self):
        self.source('a.c', self.unit())
        self.cli.unlink()
        code, summary, _ = self.run_evaluation()
        self.assertEqual(code, 1)
        self.assertEqual(summary['totals']['infrastructure_failures'], 1)


if __name__ == '__main__':
    unittest.main()
