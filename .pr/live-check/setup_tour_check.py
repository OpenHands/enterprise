"""Live check of the setup-tour fixes in #1486 on a running preview.

The setup guide is shown only to the instance's first Super Admin, which no
preview identity is. The script therefore stubs one response, GET
/api/admin/setup-state, so the guide is active for the admin identity's
Personal Workspace. Everything else (the page, driver.js, the LLM form, the
save and the profile APIs) is the real build.

    pip install playwright && python -m playwright install chromium
    APP=https://pr-<N>.staging.all-hands.dev \
    PREVIEW_ADMIN_EMAIL=... PREVIEW_ADMIN_PASSWORD=... \
    python setup_tour_check.py [fault1] [fault2] [save]

`save` adds an LLM profile to the admin's Personal Workspace and removes it
again at the end, re-activating the profile that was active before.
"""

import json
import os
import sys
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright

APP = os.environ['APP'].rstrip('/')
AUTH = APP.replace('.staging.all-hands.dev', '.auth.staging.all-hands.dev')
LOGIN = (
    f'{AUTH}/realms/allhands/protocol/openid-connect/auth?client_id=allhands'
    f'&response_type=code&redirect_uri={APP}/oauth/keycloak/callback'
    f'&state={APP}/settings&scope=openid'
)
SHOTS = os.environ.get('SHOTS', 'shots')
SAVE_STOP = 'Save the LLM profile'
POPOVER_JS = """() => { const p = document.querySelector('.driver-popover');
  if (!p) return null; const r = p.getBoundingClientRect();
  return {title: p.querySelector('.driver-popover-title')?.textContent,
          progress: p.querySelector('.driver-popover-progress-text')?.textContent,
          x: Math.round(r.x), y: Math.round(r.y)}; }"""
TRAIL_JS = """() => { window.__trail = []; new MutationObserver(() => {
  const p = document.querySelector('.driver-popover'); const r = p && p.getBoundingClientRect();
  const e = p ? [p.querySelector('.driver-popover-title')?.textContent, Math.round(r.x), Math.round(r.y)] : null;
  const last = window.__trail[window.__trail.length - 1];
  if (JSON.stringify(e) !== JSON.stringify(last)) window.__trail.push(e);
}).observe(document.body, {childList: true, subtree: true, attributes: true}); }"""
MASK_JS = """(e) => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) if (n.nodeValue.includes(e)) n.nodeValue = n.nodeValue.split(e).join('admin@…'); }"""


def check(name, ok, detail):
    print(f'{"PASS" if ok else "FAIL"} {name}: {detail}', flush=True)
    return ok


def shot(page, name):
    page.evaluate(MASK_JS, os.environ['PREVIEW_ADMIN_EMAIL'])
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=f'{SHOTS}/{name}.png')


def popover(page):
    return page.evaluate(POPOVER_JS)


def wait_title(page, title, timeout_s=30):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = popover(page)
        if state and state['title'] == title:
            return state
        page.wait_for_timeout(250)
    raise RuntimeError(f'popover "{title}" did not appear; last: {popover(page)}')


def open_session(p, guide_steps, delay_s=0.0):
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = ctx.new_page()
    page.goto(LOGIN)
    page.locator('#username').fill(os.environ['PREVIEW_ADMIN_EMAIL'])
    page.locator('#password').fill(os.environ['PREVIEW_ADMIN_PASSWORD'])
    page.locator('#kc-login').click()
    page.wait_for_url(lambda u: '/realms/' not in u, timeout=60_000)
    orgs = page.request.get(f'{APP}/api/organizations').json()['items']
    org = next(o['id'] for o in orgs if o['name'].startswith('user_'))
    state = {
        'wizard_pending': False,
        'guide_org_id': org,
        'guide_dismissed': False,
        'guide_steps': guide_steps,
    }

    def setup_state(route):
        if route.request.method != 'GET':
            return route.fallback()
        time.sleep(delay_s)
        route.fulfill(
            status=200, content_type='application/json', body=json.dumps(state)
        )

    ctx.route('**/api/admin/setup-state', setup_state)
    return browser, page, org, state


def start_from_guide(page, org):
    """Start the tour from the floating setup guide, as an admin would."""
    page.goto(f'{APP}/settings/agent?org={org}')
    page.get_by_test_id('super-admin-setup-floating-tour-add-llm').click(timeout=45_000)
    wait_title(page, 'Add an LLM profile')


def walk_to_save_stop(page):
    for _ in range(6):
        state = popover(page)
        if state and state['title'] == SAVE_STOP:
            page.wait_for_timeout(500)
            return state
        page.locator('.driver-popover-next-btn').click()
        deadline = time.monotonic() + 30
        while (
            time.monotonic() < deadline
            and (popover(page) or {}).get('title') == state['title']
        ):
            page.wait_for_timeout(250)
        page.wait_for_timeout(1000)
    raise RuntimeError(f'did not reach "{SAVE_STOP}"; last: {popover(page)}')


STEPS_OPEN = dict(
    org_llm=False, git_provider=False, mcp_server=False, automation=False, invite=False
)


def fault2(p):
    """The Save stop's element leaves the page: the tour ends, nothing in the corner."""
    browser, page, org, _ = open_session(p, dict(STEPS_OPEN))
    start_from_guide(page, org)
    walk_to_save_stop(page)
    shot(page, 'fault2_1_save_stop')
    page.evaluate(TRAIL_JS)
    page.go_back()  # browser Back: a click on Back to LLM profiles list hits driver.js's overlay
    page.wait_for_timeout(4000)
    trail = page.evaluate('() => window.__trail')
    shot(page, 'fault2_2_after_browser_back')
    ok = check(
        'fault2',
        popover(page) is None
        and page.locator('.driver-overlay').count() == 0
        and page.get_by_test_id('super-admin-setup-floating').is_visible()
        and not any(e and e[1] < 50 and e[2] < 50 for e in trail),
        f'url={page.url.split("?")[0]} popover={popover(page)} trail={trail}',
    )
    browser.close()
    return ok


def fault1(p):
    """The LLM form is open before the guide's state loads: the tour starts on stop 2/5."""
    browser, page, org, _ = open_session(p, dict(STEPS_OPEN), delay_s=4.0)
    page.goto(f'{APP}/settings/org-defaults?org={org}&setup_tour=add-llm')
    page.get_by_test_id('add-llm-profile').click(timeout=30_000)
    page.locator('.driver-popover').wait_for(timeout=30_000)
    page.wait_for_timeout(800)
    state = popover(page)
    shot(page, 'fault1_start_on_open_form')
    ok = check(
        'fault1',
        state
        and state['title'] == 'Choose a provider'
        and state['progress'] == '2 of 5',
        f'popover={state}',
    )
    browser.close()
    return ok


def save(p):
    """A real Save: the tour ends on the step event and the guide opens its next step."""
    steps = dict(STEPS_OPEN, git_provider=True, mcp_server=True, automation=True)
    browser, page, org, state = open_session(p, steps)
    profiles_url = f'{APP}/api/organizations/{org}/profiles'
    before = page.request.get(profiles_url).json()
    page.on(
        'request',
        lambda r: r.method in ('POST', 'PUT')
        and r.url.startswith(profiles_url + '/')
        and state['guide_steps'].update(org_llm=True),
    )
    try:
        start_from_guide(page, org)
        page.locator('.driver-popover-next-btn').click()
        wait_title(page, 'Choose a provider')
        for field, text in (
            ('llm-provider-input', 'openhands'),
            ('llm-model-input', 'deepseek'),
        ):
            if field == 'llm-model-input':
                page.locator('.driver-popover-next-btn').click()
                wait_title(page, 'Choose a model')
            box = page.locator(f'input[name={field}]')
            box.click()
            box.fill(text)
            page.wait_for_timeout(1500)
            page.keyboard.press('ArrowDown')
            page.keyboard.press('Enter')
            page.wait_for_timeout(1000)
        walk_to_save_stop(page)
        shot(page, 'save_1_save_stop')
        page.evaluate(TRAIL_JS)
        page.get_by_test_id('save-button').click()
        wait_title(page, 'Invite users', timeout_s=60)
        page.wait_for_timeout(1000)
        trail = page.evaluate('() => window.__trail')
        shot(page, 'save_2_next_step')
        ok = check(
            'save',
            '/settings/org-members' in page.url
            and not any(e and e[1] < 50 and e[2] < 50 for e in trail),
            f'url={page.url.split("?")[0]} trail={trail}',
        )
    finally:
        after = page.request.get(profiles_url).json()
        page.request.post(f'{profiles_url}/{quote(before["active_profile"])}/activate')
        names = {x['name'] for x in before['profiles']}
        for prof in after['profiles']:
            if prof['name'] not in names:
                page.request.delete(f'{profiles_url}/{quote(prof["name"])}')
        restored = page.request.get(profiles_url).json()
        check(
            'cleanup',
            restored == before,
            f'profiles={[x["name"] for x in restored["profiles"]]} active={restored["active_profile"]}',
        )
        browser.close()
    return ok


if __name__ == '__main__':
    checks = {'fault1': fault1, 'fault2': fault2, 'save': save}
    with sync_playwright() as pw:
        results = [checks[name](pw) for name in (sys.argv[1:] or list(checks))]
    sys.exit(0 if all(results) else 1)
