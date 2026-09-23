"""Temporarily break four configs and require the relevant public tests to fail.

Run from the repo root after the normal suite passes. Do not run concurrently
with other proxy tests: this script temporarily edits the candidate files.
"""

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = [
    (
        'nginx-header-budget',
        'nginx.conf',
        '  large_client_header_buffers 4 32k;\n',
        '',
        'nginx and credentials',
    ),
    (
        'nginx-stream-buffering',
        'nginx.conf',
        'proxy_buffering off;',
        'proxy_buffering on;',
        'nginx and sse',
    ),
    (
        'caddy-stream-reload',
        'Caddyfile',
        '      stream_close_delay 1m\n',
        '',
        'caddy and websocket',
    ),
    (
        'haproxy-path-boundary',
        'haproxy.cfg',
        'acl automation_path path /api/automation',
        'acl automation_path path_beg /api/automation',
        'haproxy and route_and_query',
    ),
]


def main():
    with tempfile.TemporaryDirectory(prefix='proxy-regressions-') as directory:
        for name, filename, before, after, selection in CASES:
            path = HERE / 'configs' / filename
            original = path.read_text()
            assert original.count(before) == 1, f'{name}: configuration has changed'
            report = Path(directory) / f'{name}.xml'
            try:
                path.write_text(original.replace(before, after))
                result = subprocess.run(
                    [
                        sys.executable,
                        '-m',
                        'pytest',
                        str(HERE),
                        f'--confcutdir={HERE}',
                        '-q',
                        '--tb=short',
                        '-k',
                        selection,
                        f'--junitxml={report}',
                    ],
                    timeout=120,
                    check=False,
                )
                suites = list(ET.parse(report).getroot().iter('testsuite'))
                assert result.returncode == 1, f'{name}: expected assertion failure'
                assert sum(int(s.attrib['errors']) for s in suites) == 0, (
                    f'{name}: setup error'
                )
                assert sum(int(s.attrib['failures']) for s in suites) == 1, (
                    f'{name}: expected exactly one failure'
                )
                print(f'DETECTED: {name}', flush=True)
            finally:
                path.write_text(original)


if __name__ == '__main__':
    main()
