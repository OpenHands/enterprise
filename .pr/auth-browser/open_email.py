"""Open a captured test email link without printing its one-time token."""

import html
import re
import subprocess
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

WORK = Path(__file__).parent
recipient = sys.argv[1]
purpose = sys.argv[2]
files = sorted(
    (WORK / 'emails').glob('*.eml'), key=lambda path: path.stat().st_mtime, reverse=True
)
for path in files:
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    if str(message['To']) != recipient:
        continue
    content = str(message.get_body(preferencelist=('html', 'plain')).get_content())
    links = re.findall(r'https://[^\s<>"\x27]+', content)
    target = next(
        (html.unescape(link) for link in links if '/auth/' + purpose in link), None
    )
    if not target:
        continue
    parsed = urlsplit(target)
    assert parsed.hostname == 'localhost'
    assert 'token' not in parse_qs(parsed.query)
    token = parse_qs(parsed.fragment)['token'][0]
    assert len(token) >= 32
    if '--inspect-only' in sys.argv:
        print(
            {
                'recipient': recipient,
                'purpose': purpose,
                'token_transport': 'fragment_only',
                'origin': parsed.netloc,
            }
        )
    else:
        result = subprocess.run(
            ['playwright-cli', '-s=auth-core-browser', 'goto', target],
            capture_output=True,
            text=True,
        )
        print(result.stdout.replace(token, '[REDACTED]'))
        print(result.stderr.replace(token, '[REDACTED]'), file=sys.stderr)
        raise SystemExit(result.returncode)
    break
else:
    raise SystemExit('No matching captured email')
