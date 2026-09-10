"""Real HTTP device/SDK settings/lifecycle probes; never prints bearer material."""

import json
import sys
from pathlib import Path

import httpx

WORK = Path(__file__).parent
STATE = json.loads((WORK / 'state.json').read_text())
ORIGIN = f'https://localhost:{STATE["app_port"]}'
ADMIN = 'admin@auth-test.example'
MEMBER = 'member@auth-test.example'
MEMBER_ID = '82396d1b-8198-4275-972a-ae0a386b92de'


def client():
    return httpx.Client(base_url=ORIGIN, verify=str(WORK / 'localhost.crt'), timeout=60)


def post(browser, path, *, form=None, **payload):
    seed = browser.get('/api/auth/csrf')
    assert seed.status_code == 200
    headers = {'Origin': ORIGIN, 'X-CSRF-Token': seed.json()['csrf_token']}
    return browser.post(
        path,
        headers=headers,
        **({'data': form} if form is not None else {'json': payload}),
    )


def login(browser, email, password):
    result = post(
        browser, '/api/auth/login', email=email, password=password, redirect_url='/'
    )
    assert result.status_code == 200, result.text
    return result.json()


def sdk_and_lifecycle():
    with client() as admin, client() as member, client() as device:
        login(admin, ADMIN, 'Synthetic changed password 2026')
        if '--member-already-changed' in sys.argv:
            login(member, MEMBER, 'Synthetic member changed password 2026')
        else:
            restricted = login(member, MEMBER, 'Synthetic member password 2026')
            assert restricted['password_change_required'] is True
            assert member.get('/api/v1/users/me').status_code == 403
            changed = post(
                member,
                '/api/auth/password/change',
                current_password='Synthetic member password 2026',
                new_password='Synthetic member changed password 2026',
                redirect_url='/',
            )
            assert changed.status_code == 200, changed.text
        tos = post(member, '/api/accept_tos', redirect_url='/')
        assert tos.status_code == 200, tos.text

        auth = device.post('/oauth/device/authorize')
        assert auth.status_code == 200, auth.text
        codes = auth.json()
        verified = post(
            member,
            '/oauth/device/verify-authenticated',
            form={'user_code': codes['user_code']},
        )
        assert verified.status_code == 200, verified.text
        issued = device.post(
            '/oauth/device/token', data={'device_code': codes['device_code']}
        )
        assert issued.status_code == 200, issued.text
        key = issued.json()['access_token']
        headers = {'Authorization': 'Bearer ' + key}
        settings = device.get('/api/v1/users/me', headers=headers)
        assert settings.status_code == 200, settings.text
        assert 'synthetic-local-model-key' not in settings.text
        assert (
            device.get(
                '/api/v1/users/me?expose_secrets=true', headers=headers
            ).status_code
            == 401
        )
        assert (
            device.get(
                '/api/v1/users/me?expose_secrets=true',
                headers={**headers, 'X-Session-API-Key': 'invalid'},
            ).status_code
            == 401
        )
        print(
            json.dumps(
                {
                    'device_flow': 'passed',
                    'sdk_masked_settings': settings.status_code,
                    'sdk_unowned_secret_exposure': 'denied',
                }
            ),
            flush=True,
        )

        forbidden = post(
            member,
            '/api/auth/accounts',
            email='forbidden@auth-test.example',
            initial_password='Synthetic forbidden password 2026',
        )
        assert forbidden.status_code == 403, forbidden.text
        disabled = post(admin, f'/api/admin/users/{MEMBER_ID}/disable')
        assert disabled.status_code == 200, disabled.text
        assert member.get('/api/v1/users/me').status_code == 401
        assert device.get('/api/v1/users/me', headers=headers).status_code == 401
        failed = post(
            member,
            '/api/auth/login',
            email=MEMBER,
            password='Synthetic member changed password 2026',
            redirect_url='/',
        )
        assert failed.status_code == 401, failed.text
        print(
            json.dumps(
                {
                    'ordinary_account_create': forbidden.status_code,
                    'disable': disabled.status_code,
                    'disabled_cookie_and_api_key': 'rejected',
                    'disabled_login': failed.status_code,
                }
            ),
            flush=True,
        )


def owned_sdk():
    with client() as admin, client() as device:
        login(admin, ADMIN, 'Synthetic changed password 2026')
        codes = device.post('/oauth/device/authorize').json()
        assert codes['verification_uri'].startswith(ORIGIN + '/')
        verified = post(
            admin,
            '/oauth/device/verify-authenticated',
            form={'user_code': codes['user_code']},
        )
        assert verified.status_code == 200, verified.text
        issued = device.post(
            '/oauth/device/token', data={'device_code': codes['device_code']}
        )
        assert issued.status_code == 200, issued.text
        headers = {'Authorization': 'Bearer ' + issued.json()['access_token']}
        conversation = admin.get(
            '/api/v1/app-conversations',
            params={'ids': '5daaf17b-44b9-47d5-9527-74f5a1842d34'},
        )
        assert conversation.status_code == 200
        sandbox_key = conversation.json()[0]['session_api_key']
        assert sandbox_key
        ordinary = device.get('/api/v1/users/me', headers=headers)
        assert ordinary.status_code == 200
        assert 'synthetic-local-model-key' not in ordinary.text
        exposed = device.get(
            '/api/v1/users/me?expose_secrets=true',
            headers={
                **headers,
                'X-Session-API-Key': sandbox_key,
            },
        )
        assert exposed.status_code == 200, exposed.text
        assert 'synthetic-local-model-key' in exposed.text
        print(
            json.dumps(
                {
                    'device_origin': 'same_https_origin',
                    'sdk_masked': ordinary.status_code,
                    'sdk_owned_running_sandbox_expose': exposed.status_code,
                    'synthetic_llm_key_matches': True,
                }
            ),
            flush=True,
        )


def final_sdk_inheritance():
    def mint(browser, device):
        codes = device.post('/oauth/device/authorize').json()
        result = post(
            browser,
            '/oauth/device/verify-authenticated',
            form={'user_code': codes['user_code']},
        )
        assert result.status_code == 200, result.text
        issued = device.post(
            '/oauth/device/token', data={'device_code': codes['device_code']}
        )
        assert issued.status_code == 200, issued.text
        return {'Authorization': 'Bearer ' + issued.json()['access_token']}

    with client() as admin, client() as member, client() as device, client() as sandbox:
        login(
            admin, 'admin-renamed@auth-test.example', 'Synthetic operator password 2026'
        )
        assert post(admin, f'/api/admin/users/{MEMBER_ID}/enable').status_code == 200
        try:
            login(member, MEMBER, 'Synthetic member changed password 2026')
            admin_headers = mint(admin, device)
            member_headers = mint(member, device)
            conversation = admin.get(
                '/api/v1/app-conversations',
                params={'ids': '5daaf17b-44b9-47d5-9527-74f5a1842d34'},
            ).json()[0]
            sandbox_key = conversation['session_api_key']
            assert sandbox_key
            exposed = device.get(
                '/api/v1/users/me?expose_secrets=true',
                headers={
                    **admin_headers,
                    'X-Session-API-Key': sandbox_key,
                },
            )
            assert exposed.status_code == 200, exposed.text
            assert 'synthetic-local-model-key' in exposed.text
            foreign = device.get(
                '/api/v1/users/me?expose_secrets=true',
                headers={
                    **member_headers,
                    'X-Session-API-Key': sandbox_key,
                },
            )
            assert foreign.status_code == 403, foreign.text
            sandbox_id = conversation['sandbox_id']
            path = f'/api/v1/sandboxes/{sandbox_id}/settings/secrets'
            assert sandbox.get(path).status_code == 401
            assert (
                sandbox.get(path, headers={'X-Session-API-Key': 'invalid'}).status_code
                == 401
            )
            headers = {'X-Session-API-Key': sandbox_key}
            names = sandbox.get(path, headers=headers)
            assert names.status_code == 200, names.text
            assert 'github_token' in [
                secret['name'] for secret in names.json()['secrets']
            ]
            secret = sandbox.get(path + '/github_token', headers=headers)
            assert secret.status_code == 200, secret.text
            assert secret.text == 'synthetic-github-token'
            wrong_sandbox = sandbox.get(
                '/api/v1/sandboxes/not-owned/settings/secrets/github_token',
                headers=headers,
            )
            assert wrong_sandbox.status_code == 403, wrong_sandbox.text
            print(
                json.dumps(
                    {
                        'owned_running_sandbox_expose': exposed.status_code,
                        'non_owned_valid_sandbox_key': foreign.status_code,
                        'sandbox_secret_names': names.status_code,
                        'sandbox_secret_value': secret.status_code,
                        'synthetic_provider_secret_matches': True,
                        'wrong_sandbox_id': wrong_sandbox.status_code,
                    }
                ),
                flush=True,
            )
        finally:
            assert (
                post(admin, f'/api/admin/users/{MEMBER_ID}/disable').status_code == 200
            )


if __name__ == '__main__':
    if '--final-sdk' in sys.argv:
        final_sdk_inheritance()
    elif '--owned-sdk' in sys.argv:
        owned_sdk()
    else:
        sdk_and_lifecycle()
