"""Server-rendered reviewer sign-in; no dependency on the public login SPA."""

import base64
import hashlib
import html

from fastapi.responses import HTMLResponse

from server.routes.external_mcp_consent_page import CONSENT_STYLES

_STYLES = (
    CONSENT_STYLES
    + """
form { flex-direction: column; gap: 8px; }
label { margin-top: 8px; font-size: 14px; }
input { width: 100%; padding: 12px; border: 1px solid #525252; border-radius: 6px;
  background: #181818; color: #ececec; font: inherit; }
input:focus-visible { outline: 2px solid #cfb755; outline-offset: 2px; }
button { margin-top: 16px; }
.error { color: #fca5a5; margin-bottom: 16px; }
"""
)
_STYLE_HASH = base64.b64encode(hashlib.sha256(_STYLES.encode()).digest()).decode()


def render_login_page(csrf: str, *, failed: bool = False) -> HTMLResponse:
    error = (
        '<p class="error" role="alert">Unable to sign in with those credentials.</p>'
        if failed
        else ''
    )
    content = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        '<title>Sign in to OpenHands</title>'
        f'<style>{_STYLES}</style></head><body><main>'
        '<p class="brand">OpenHands</p><h1>Reviewer sign in</h1>'
        f'{error}<form method="post" action="/oauth/reviewer/login">'
        f'<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">'
        '<label for="email">Email</label>'
        '<input id="email" name="email" type="email" autocomplete="username" maxlength="320" required>'
        '<label for="password">Password</label>'
        '<input id="password" name="password" type="password" autocomplete="current-password" maxlength="4096" required>'
        '<button class="allow" type="submit">Sign in</button>'
        '</form></main></body></html>'
    )
    return HTMLResponse(
        content,
        status_code=401 if failed else 200,
        headers={
            'Cache-Control': 'no-store',
            'Referrer-Policy': 'no-referrer',
            'X-Robots-Tag': 'noindex, nofollow',
            'Content-Security-Policy': (
                f"default-src 'none'; style-src 'sha256-{_STYLE_HASH}'; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )
