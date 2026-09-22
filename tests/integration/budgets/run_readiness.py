"""Run every backend budget contract, including non-default safety probes."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--junitxml', type=Path, default=Path('.pr/budget-readiness.xml')
    )
    parser.add_argument('--collect-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    suite = Path(__file__).resolve().parent
    files = sorted({*suite.glob('test_*.py'), *suite.glob('probe_*.py')})
    if not files:
        raise RuntimeError('No budget contracts found')
    report = args.junitxml.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)
    command = [sys.executable, '-m', 'pytest', *map(str, files), '-n', '0', '-ra']
    if args.collect_only:
        command.append('--collect-only')
    else:
        command.append(f'--junitxml={report}')
    # Do not inherit filters that can silently omit required safety contracts.
    env = {**os.environ, 'PYTEST_ADDOPTS': ''}
    result = subprocess.run(command, cwd=root, env=env, check=False)
    if result.returncode or args.collect_only:
        return result.returncode
    cases = ET.parse(report).findall('.//testcase')
    if not cases or any(case.find('skipped') is not None for case in cases):
        print(
            'NOT READY: empty suite, skipped tests, or expected failures',
            file=sys.stderr,
        )
        return 1
    print(
        'Backend budget contracts passed. Browser/agent release sign-off is still separate.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
