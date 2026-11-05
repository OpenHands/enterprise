"""Run budget regressions, informational known issues, or the full release gate."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def write_summary(suite: str, cases: list[ET.Element], exit_code: int) -> None:
    lines = [f'## Budget suite: {suite}', '', f'Pytest exit code: {exit_code}', '']
    if suite == 'known-issues':
        lines += [
            'Informational: failures do not block PRs. Passing cases are candidates',
            'for promotion after they also pass on main. This is not release sign-off.',
            '',
        ]
    lines += ['| Test | Result | Issue |', '| --- | --- | --- |']
    for case in cases:
        result = 'PASS'
        for tag in ('failure', 'error', 'skipped'):
            if case.find(tag) is not None:
                result = tag.upper()
                break
        issue = case.find("./properties/property[@name='budget_issue']")
        issue_id = issue.get('value', '') if issue is not None else ''
        link = (
            f'[{issue_id}](https://linear.app/all-hands-ai/issue/{issue_id})'
            if issue_id
            else ''
        )
        name = case.get('name', '').replace('|', r'\|')
        lines.append(f'| {name} | {result} | {link} |')
    if not cases:
        lines.append('\nNo test results recorded.')
    summary = '\n'.join(lines) + '\n'
    print(summary)
    if summary_path := os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(summary_path).open('a') as output:
            output.write(summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--suite', choices=('all', 'regression', 'known-issues'), default='all'
    )
    parser.add_argument('--junitxml', type=Path)
    parser.add_argument('--collect-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    suite = Path(__file__).resolve().parent
    files = sorted({*suite.glob('test_*.py'), *suite.glob('probe_*.py')})
    if not files:
        raise RuntimeError('No budget contracts found')
    report = (args.junitxml or Path(f'.pr/budget-{args.suite}.xml')).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    command = [sys.executable, '-m', 'pytest', *map(str, files), '-n', '0', '-ra']
    if args.suite != 'all':
        expression = 'budget_known_issue'
        if args.suite == 'regression':
            expression = f'not {expression}'
        command.extend(['-m', expression])
    if args.collect_only:
        command.append('--collect-only')
    else:
        command.append(f'--junitxml={report}')
    # Do not inherit filters that can silently omit required safety contracts.
    env = {**os.environ, 'PYTEST_ADDOPTS': ''}
    result = subprocess.run(command, cwd=root, env=env, check=False)
    if args.collect_only:
        return result.returncode
    if not report.exists():
        write_summary(args.suite, [], result.returncode)
        print('No JUnit report; budget validation did not complete.', file=sys.stderr)
        return result.returncode or 1
    cases = ET.parse(report).findall('.//testcase')
    write_summary(args.suite, cases, result.returncode)
    # Once all known cases graduate, this informational group may be empty.
    if args.suite == 'known-issues' and not cases and result.returncode == 5:
        return 0
    if result.returncode:
        return result.returncode
    if not cases or any(
        case.find(tag) is not None
        for case in cases
        for tag in ('failure', 'error', 'skipped')
    ):
        print(
            'NOT READY: empty suite, skipped tests, or expected failures',
            file=sys.stderr,
        )
        return 1
    print(f'Budget {args.suite} suite passed; this is not browser/agent sign-off.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
