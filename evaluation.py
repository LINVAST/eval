#!/usr/bin/env python3
"""Run evaluation with checked producers and explicit outcome accounting."""

import argparse
import configparser
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


LANGUAGES = {'c': 'c', 'go': 'go', 'java': 'java', 'kotlin': 'kt',
             'lua': 'lua', 'python': 'py'}
EXCLUSIONS = ('tests', 'test', 'vendor', 'node_modules')
OUTCOMES = {0: 'accepted_asts', 10: 'parser_rejections',
            11: 'builder_rejections', 12: 'unsupported_language_rejections',
            13: 'builder_errors'}
COUNTERS = ('files_discovered', 'files_excluded', 'files_extracted',
            'extraction_failures', 'zero_match_files', 'matched_constructs',
            'unique_constructs', 'duplicate_constructs',
            'whole_files_submitted', 'units_attempted', 'parser_rejections',
            'builder_rejections', 'builder_errors', 'cli_errors',
            'unsupported_language_rejections', 'timeouts',
            'accepted_asts', 'infrastructure_failures')


class InfrastructureError(Exception):
    pass


class Evaluation:
    def __init__(self, root, cli, extractor, output, timeout=60, whole_files=False):
        self.root = Path(root)
        self.cli = Path(cli)
        self.extractor = Path(extractor)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.whole_files = whole_files
        self.total = dict.fromkeys(COUNTERS, 0)
        self.projects = {}
        self.project = None
        self.context = {}
        self.dependencies = {}
        self.seen_constructs = {}

    def unique_construct(self, lang, unit, index):
        digest = hashlib.sha256(unit['wrapped'].encode('utf-8')).hexdigest()
        key = (lang, digest)
        identity = {**self.context, 'unit_index': index, **{
            field: unit[field] for field in ('name', 'type', 'startLine', 'endLine')
        }}
        if key in self.seen_constructs:
            self.count('duplicate_constructs')
            self.event('duplicate_construct', unit_index=index, sha256=digest,
                       duplicate_of=self.seen_constructs[key], **{
                           field: unit[field] for field in ('name', 'type', 'startLine', 'endLine')
                       })
            return False
        self.seen_constructs[key] = identity
        self.count('unique_constructs')
        return True

    def event(self, event, **details):
        record = {'event': event, **self.context, **details}
        with (self.output / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record) + '\n')

    def count(self, key, amount=1):
        self.total[key] += amount
        if self.project is not None:
            self.projects[self.project][key] += amount

    def fail(self, stage, reason, **details):
        self.count('infrastructure_failures')
        self.event('infrastructure_failure', stage=stage, reason=reason, **details)
        raise InfrastructureError(f'{stage}: {reason}')

    def inventory(self, targets):
        registered = None
        modules = self.root / '.gitmodules'
        if modules.is_file():
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(modules.read_text())
            registered = sorted(
                self.root / section['path'] for section in config.values()
                if section.get('path', '').startswith('lang/')
            )
        projects = []
        for target in targets:
            base = self.root / target
            if target in ('.', 'lang'):
                projects.extend(registered if registered is not None
                                else sorted((self.root / 'lang').glob('*/*/')))
            elif base.parent == self.root / 'lang':
                projects.extend([p for p in registered if p.parent == base]
                                if registered is not None
                                else sorted(p for p in base.iterdir() if p.is_dir()))
            else:
                if registered is not None and not any(
                    base == p or p in base.parents for p in registered
                ):
                    self.fail('configuration', f'not a configured corpus project: {target}')
                projects.append(base)
        plan = []
        seen = set()
        for project in dict.fromkeys(projects):
            relative = project.relative_to(self.root)
            self.project = str(relative)
            self.context = {'project': self.project}
            self.projects[self.project] = dict.fromkeys(COUNTERS, 0)
            lang = relative.parts[1]
            extension = LANGUAGES.get(lang, lang)
            try:
                found = subprocess.run(
                    ['fd', '--type', 'f', '-0', '-e', extension], cwd=project,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                self.fail('discovery', str(error), stderr=getattr(error, 'stderr', b'').decode(errors='replace'))
            files = []
            for entry in sorted(found.stdout.split(b'\0')):
                if not entry:
                    continue
                path = project / os.fsdecode(entry)
                if path in seen:
                    continue
                seen.add(path)
                name = str(path.relative_to(project))
                self.count('files_discovered')
                self.event('file_discovered', file=name)
                exclusion = next((part for part in EXCLUSIONS if part in name), None)
                if exclusion:
                    self.count('files_excluded')
                    self.event('file_excluded', file=name, reason=f'path contains {exclusion}')
                else:
                    files.append(path)
            plan.append((project, lang, extension, files))
        self.project = None
        self.context = {}
        return plan

    def extract(self, lang, path):
        try:
            self.event('extraction_started')
            result = subprocess.run(
                [sys.executable, str(self.extractor), lang, str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            with (self.output / 'diagnostics.log').open('ab') as diagnostics:
                diagnostics.write((json.dumps(self.context) + '\n').encode())
                diagnostics.write(result.stderr)
            if result.returncode:
                self.count('extraction_failures')
                self.event('extraction_failure', exit_code=result.returncode)
                self.fail('extraction', 'extractor exited unsuccessfully', exit_code=result.returncode)
            # Do not count or evaluate any partial output from a failed producer.
            units = [json.loads(line) for line in result.stdout.decode('utf-8').splitlines() if line.strip()]
            for unit in units:
                if not isinstance(unit, dict) or not all(
                    isinstance(unit.get(key), str) for key in ('code', 'wrapped', 'type')
                ) or 'name' not in unit or not (unit['name'] is None or isinstance(unit['name'], str)) or not all(
                    type(unit.get(key)) is int and unit[key] > 0 for key in ('startLine', 'endLine')
                ) or unit['endLine'] < unit['startLine']:
                    raise ValueError('invalid extraction record')
        except (OSError, UnicodeError, ValueError) as error:
            self.count('extraction_failures')
            self.event('extraction_failure', reason=str(error))
            self.fail('extraction', str(error))
        self.count('files_extracted')
        self.count('matched_constructs', len(units))
        if not units:
            self.count('zero_match_files')
        self.event('extraction_complete', matched_constructs=len(units))
        return units

    def evaluate_unit(self, path, **identity):
        self.count('units_attempted')
        with (self.output / 'diagnostics.log').open('ab') as diagnostics:
            diagnostics.write((json.dumps({**self.context, **identity}) + '\n').encode())
            diagnostics.flush()
            try:
                with subprocess.Popen(
                    [str(self.cli), 'ast', str(path), '-o', os.devnull, '-c'],
                    stdout=diagnostics, stderr=diagnostics, start_new_session=True,
                ) as process:
                    try:
                        code = process.wait(timeout=self.timeout)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        self.count('timeouts')
                        self.event('unit_result', result='timeout', timeout_seconds=self.timeout, **identity)
                        return
            except OSError as error:
                self.fail('cli', str(error), **identity)
        outcome = OUTCOMES.get(code)
        if outcome is None:
            if code == 70:
                self.fail('cli', 'CLI input/output or configuration failure', exit_code=code, **identity)
            outcome = 'cli_errors'
        self.count(outcome)
        self.event('unit_result', result=outcome, exit_code=code, **identity)

    def build(self):
        print('Building linvast-cli', flush=True)
        self.event('build_started')
        with (self.output / 'diagnostics.log').open('ab') as diagnostics:
            try:
                result = subprocess.run(
                    ['dotnet', 'publish', str(self.root / 'CLI/CLI/CLI.csproj'),
                     '--artifacts-path', str(self.root / 'results/build'),
                     '-r', 'linux-x64', '-p:PublishSingleFile=true', '--self-contained',
                     'false', '-o', str(self.cli.parent)],
                    cwd=self.root / 'CLI', stdout=diagnostics, stderr=diagnostics,
                )
            except OSError as error:
                self.fail('build', str(error))
        if result.returncode:
            self.fail('build', 'dotnet publish failed', exit_code=result.returncode)
        if not self.cli.is_file() or not os.access(self.cli, os.X_OK):
            self.fail('build', 'published CLI is missing or not executable')
        assets = json.loads((self.root / 'results/build/obj/CLI/project.assets.json').read_text())
        for name, library in assets['libraries'].items():
            if name.startswith('LINVAST/') or name.startswith('LINVAST.'):
                path = library.get('path')
                if library['type'] == 'project' and path:
                    path = str((self.root / 'CLI/CLI' / path).resolve())
                self.dependencies[name] = {'type': library['type'], 'path': path}
        self.event('build_complete', dependencies=self.dependencies)

    def run(self, targets, build=True):
        status = 'aborted'
        try:
            if not math.isfinite(self.timeout) or self.timeout <= 0:
                self.fail('configuration', 'FUN_TIMEOUT must be a positive number of seconds')
            plan = self.inventory(targets)
            if build:
                self.build()
            with tempfile.TemporaryDirectory(prefix='linvast-wrapper.') as directory:
                for project, lang, extension, files in plan:
                    self.project = str(project.relative_to(self.root))
                    print(f'--- {self.project} ({extension}) ---', flush=True)
                    for path in files:
                        self.context = {'project': self.project, 'file': str(path.relative_to(project))}
                        if self.whole_files or lang not in LANGUAGES:
                            self.count('whole_files_submitted')
                            self.evaluate_unit(path, mode='whole_file')
                            continue
                        units = self.extract(lang, path)
                        print(f'  [{self.context["file"]}] matched constructs: {len(units)}', flush=True)
                        for index, unit in enumerate(units):
                            if not self.unique_construct(lang, unit, index):
                                continue
                            wrapper = Path(directory) / f'wrapper.{extension}'
                            wrapper.write_bytes(unit['wrapped'].encode('utf-8'))
                            self.evaluate_unit(wrapper, unit_index=index, **{
                                key: unit[key] for key in ('name', 'type', 'startLine', 'endLine')
                            })
                    self.context = {'project': self.project}
                    self.event('project_complete', counts=self.projects[self.project])
            status = 'complete'
        except InfrastructureError as error:
            print(f'Evaluation aborted: {error}', file=sys.stderr)
        except Exception as error:
            self.count('infrastructure_failures')
            self.event('infrastructure_failure', stage='runner', reason=str(error))
            print(f'Evaluation aborted: {error}', file=sys.stderr)
        finally:
            summary = {'status': status, 'dependencies': self.dependencies,
                       'selection': {'declarations': 'whole_file' if self.whole_files else 'all declarations with extracted children removed',
                                     'deduplication': 'none' if self.whole_files else 'language and exact wrapped UTF-8 input'},
                       'totals': self.total, 'projects': self.projects}
            (self.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
            self.context = {}
            self.event('run_finished', **summary)
            for project, counts in self.projects.items():
                print(f'{project} summary: {json.dumps(counts)}')
            print(f'=== TOTAL ({status}) ===\n{json.dumps(self.total)}')
            print(f'Logs and summary: {self.output}')
        return 0 if status == 'complete' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--whole-files', action='store_true')
    parser.add_argument('targets', nargs='+')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    results = root / 'results'
    results.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='evaluation-', dir=results))
    try:
        timeout = float(os.environ.get('FUN_TIMEOUT', '60'))
    except ValueError:
        timeout = float('nan')
    print(f'Logs and summary: {output}', flush=True)
    return Evaluation(root, root / 'CLI/CLI/bin/Release/eval-publish/CLI',
                      root / 'extractors/extract-functions.py', output,
                      timeout, args.whole_files).run(args.targets)


if __name__ == '__main__':
    sys.exit(main())
