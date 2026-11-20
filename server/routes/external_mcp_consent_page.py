"""Standalone consent UI, using the device authorization page's visual theme."""

import base64
import hashlib
import html

CONSENT_STYLES = """
:root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif;
  background: #181818; color: #ececec; }
* { box-sizing: border-box; }
body { margin: 0; min-height: 100svh; display: grid; place-items: center; padding: 24px 16px; }
main { width: 100%; max-width: 448px; padding: 24px; background: #202020;
  border: 1px solid #242424; border-radius: 16px; box-shadow: 0 8px 24px #0003; }
.brand { margin: 0 0 24px; text-align: center; font-size: 14px; font-weight: 600;
  letter-spacing: .04em; color: #cfb755; }
h1 { margin: 0 0 24px; font-size: 24px; line-height: 1.35; text-align: center;
  font-weight: 600; overflow-wrap: anywhere; }
p { font-size: 14px; line-height: 1.6; margin: 0; }
.permissions { background: #171717; border: 1px solid #404040; border-radius: 8px;
  padding: 16px; margin-bottom: 20px; }
h2 { font-size: 14px; margin: 0 0 8px; font-weight: 600; }
.permissions p { color: #d1d5db; }
.warning { background: #3a2a06; border: 1px solid #f59e0b; border-radius: 12px;
  padding: 14px; margin-bottom: 24px; }
.warning h2 { color: #fbbf24; }
.return { color: #a1a1aa; text-align: center; margin-bottom: 24px; overflow-wrap: anywhere; }
form { display: flex; gap: 12px; }
button { flex: 1; min-height: 44px; padding: 10px 16px; border-radius: 6px;
  font: inherit; font-size: 14px; font-weight: 500; cursor: pointer; }
.deny { border: 1px solid #525252; background: transparent; color: #d1d5db; }
.deny:hover { background: #292929; }
.allow { border: 1px solid #fff; background: #fff; color: #181818; }
.allow:hover { background: #e5e5e5; }
a.continue { display: block; min-height: 44px; padding: 12px 16px; border-radius: 6px;
  background: #fff; color: #181818; text-align: center; text-decoration: none;
  font-size: 14px; font-weight: 500; }
a.continue:focus-visible, button:focus-visible { outline: 2px solid #cfb755; outline-offset: 4px; }
"""
CONSENT_STYLE_HASH = base64.b64encode(
    hashlib.sha256(CONSENT_STYLES.encode()).digest()
).decode()


def render_consent_page(
    client_name: str,
    return_host: str,
    handle: str,
    csrf: str,
    *,
    can_write: bool,
    consent_path: str,
) -> str:
    client_name, return_host, handle, csrf, consent_path = (
        html.escape(value, quote=True)
        for value in (client_name, return_host, handle, csrf, consent_path)
    )
    warning = (
        '<section class="warning" aria-labelledby="access-title">'
        '<h2 id="access-title">Only connect a tool you trust</h2>'
        f'<p>{client_name} will also be able to start new tasks '
        'and send follow-up messages. Those tasks run agents with your '
        'access, including your connected Git accounts and secrets.</p></section>'
        if can_write
        else ''
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Connect {client_name} to OpenHands</title>'
        f'<style>{CONSENT_STYLES}</style></head><body><main>'
        '<p class="brand">OpenHands</p>'
        f'<h1>Connect {client_name} to OpenHands?</h1>'
        '<section class="permissions" aria-labelledby="permissions-title">'
        '<h2 id="permissions-title">Task access</h2>'
        f'<p>Allow {client_name} to read your OpenHands tasks in your current '
        'organization. You can disconnect it later.</p></section>'
        f'{warning}'
        f'<p class="return">After you choose, you will be sent back to {return_host}.</p>'
        f'<form method="post" action="{consent_path}">'
        f'<input type="hidden" name="handle" value="{handle}">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        '<button class="deny" name="decision" value="deny" type="submit">Deny</button>'
        '<button class="allow" name="decision" value="allow" type="submit">Allow</button>'
        '</form></main></body></html>'
    )


def render_callback_handoff(destination: str) -> str:
    """Finish the form navigation before navigating to the registered client.

    The callback can itself redirect to another origin, such as the client's
    login provider. Chromium checks that entire HTTP redirect chain against
    the original consent form's form-action. A new document navigation avoids
    that constraint without allowing arbitrary form submission destinations.
    """
    destination = html.escape(destination, quote=True)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="refresh" content="0;url={destination}">'
        '<title>Return to your connected app</title>'
        f'<style>{CONSENT_STYLES}</style></head><body><main>'
        '<p class="brand">OpenHands</p>'
        '<h1>Returning to your app</h1>'
        '<p class="return">If you are not redirected automatically, choose Continue.</p>'
        f'<a class="continue" id="continue" href="{destination}">Continue</a>'
        '</main></body></html>'
    )
