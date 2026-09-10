"""Disposable live Keycloak compatibility harness; no product monkeypatches."""

import json
import os
import secrets
import socket
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / '.pr/auth-keycloak'
STATE = WORK / 'state.json'


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def environment():
    state = json.loads(STATE.read_text())
    env = {
        key: os.environ[key]
        for key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL')
        if key in os.environ
    }
    env.update(
        {
            'PYTHON_DOTENV_DISABLED': '1',
            'PYTHONPATH': str(ROOT),
            'PYTHONUNBUFFERED': '1',
            'DB_HOST': '127.0.0.1',
            'DB_PORT': '32769',
            'DB_USER': 'postgres',
            'DB_PASS': 'postgres',
            'DB_NAME': state['database'],
            'DB_DRIVER': 'pg8000',
            'DB_SSL_MODE': 'disable',
            'REDIS_HOST': '127.0.0.1',
            'REDIS_PORT': str(state['redis_port']),
            'REDIS_PASSWORD': '',
            'OPENHANDS_CONFIG_CLS': 'server.config.SaaSServerConfig',
            'OH_APP_MODE': 'saas',
            'OH_DEPLOYMENT_MODE': 'self_hosted',
            'OH_PERSISTENCE_DIR': str(WORK / 'persistence'),
            'WEB_HOST': state['app_url'],
            'OH_WEB_URL': state['app_url'],
            'PERMITTED_CORS_ORIGINS': state['app_url'],
            'JWT_SECRET': state['jwt_secret'],
            'KEYCLOAK_SERVER_URL': state.get('internal_kc_url', state['kc_url']),
            'KEYCLOAK_SERVER_URL_EXT': state['kc_url'],
            'KEYCLOAK_REALM_NAME': 'enterprise',
            'KEYCLOAK_CLIENT_ID': 'openhands',
            'KEYCLOAK_CLIENT_SECRET': state['client_secret'],
            'KEYCLOAK_ADMIN_PASSWORD': state['admin_password'],
            'KEYCLOAK_REQUEST_TIMEOUT': '2',
            'KEYCLOAK_MAX_RETRIES': '0',
            'ENABLE_ENTERPRISE_SSO': 'true',
            'OPENHANDS_LLM_PROVIDER_ROUTE': 'direct',
            'OPENHANDS_DEFAULT_LLM_MODEL': 'openai/gpt-4o-mini',
            'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
            'POSTHOG_CLIENT_KEY': '',
            'LOG_ALL_EVENTS': 'false',
            'OH_ENABLE_ONBOARDING': 'false',
        }
    )
    return state, env


def setup():
    WORK.mkdir(parents=True, exist_ok=True)
    assert not STATE.exists(), 'Refusing to replace an existing harness identity'
    suffix = uuid.uuid4().hex[:12]
    state = {
        'database': f'auth_keycloak_{suffix}',
        'kc_name': f'oh-auth-keycloak-{suffix}',
        'redis_name': f'oh-auth-keycloak-redis-{suffix}',
        'kc_port': port(),
        'redis_port': port(),
        'app_port': port(),
        'legacy_user_id': str(uuid.uuid4()),
        'legacy_email': 'legacy@keycloak-qa.example',
        'broker_email': 'broker@keycloak-qa.example',
        'password': secrets.token_urlsafe(28),
        'admin_password': secrets.token_urlsafe(28),
        'client_secret': secrets.token_urlsafe(32),
        'broker_secret': secrets.token_urlsafe(32),
        'jwt_secret': secrets.token_urlsafe(48),
        'api_key': 'sk-oh-' + secrets.token_urlsafe(24),
        'legacy_llm_secret': 'synthetic-' + secrets.token_urlsafe(24),
    }
    state['app_url'] = f'https://localhost:{state["app_port"]}'
    state['kc_url'] = f'http://localhost:{state["kc_port"]}'
    STATE.write_text(json.dumps(state, indent=2))
    STATE.chmod(0o600)
    imports = WORK / 'realms'
    imports.mkdir(exist_ok=True)
    client = {
        'clientId': 'openhands',
        'secret': state['client_secret'],
        'protocol': 'openid-connect',
        'publicClient': False,
        'standardFlowEnabled': True,
        'directAccessGrantsEnabled': True,
        'redirectUris': [state['app_url'] + '/*'],
        'webOrigins': [state['app_url']],
        'defaultClientScopes': [
            'basic',
            'web-origins',
            'acr',
            'roles',
            'profile',
            'email',
        ],
        'optionalClientScopes': ['offline_access'],
        'protocolMappers': [
            {
                'name': 'identity_provider',
                'protocol': 'openid-connect',
                'protocolMapper': 'oidc-usermodel-attribute-mapper',
                'config': {
                    'user.attribute': 'identity_provider',
                    'claim.name': 'identity_provider',
                    'jsonType.label': 'String',
                    'access.token.claim': 'true',
                    'id.token.claim': 'true',
                    'userinfo.token.claim': 'true',
                },
            }
        ],
    }

    def user(email, **extra):
        return {
            'username': email,
            'email': email,
            'enabled': True,
            'emailVerified': True,
            'firstName': 'Synthetic',
            'lastName': 'Compatibility',
            'credentials': [
                {'type': 'password', 'value': state['password'], 'temporary': False}
            ],
            **extra,
        }

    upstream = {
        'realm': 'synthetic-upstream',
        'enabled': True,
        'sslRequired': 'none',
        'clients': [
            {
                'clientId': 'enterprise-broker',
                'secret': state['broker_secret'],
                'protocol': 'openid-connect',
                'standardFlowEnabled': True,
                'redirectUris': [
                    state['kc_url']
                    + '/realms/enterprise/broker/enterprise_sso/endpoint'
                ],
            }
        ],
        'users': [user(state['broker_email'])],
    }
    upstream_base = state['kc_url'] + '/realms/synthetic-upstream'
    enterprise = {
        'realm': 'enterprise',
        'enabled': True,
        'sslRequired': 'none',
        'accessTokenLifespan': 10,
        'ssoSessionIdleTimeout': 600,
        'ssoSessionMaxLifespan': 1800,
        'clients': [client],
        'users': [user(state['legacy_email'], id=state['legacy_user_id'])],
        'identityProviders': [
            {
                'alias': 'enterprise_sso',
                'providerId': 'oidc',
                'enabled': True,
                'trustEmail': True,
                'storeToken': False,
                'firstBrokerLoginFlowAlias': 'first broker login',
                'config': {
                    'clientId': 'enterprise-broker',
                    'clientSecret': state['broker_secret'],
                    'authorizationUrl': upstream_base + '/protocol/openid-connect/auth',
                    'tokenUrl': upstream_base + '/protocol/openid-connect/token',
                    'userInfoUrl': upstream_base + '/protocol/openid-connect/userinfo',
                    'jwksUrl': upstream_base + '/protocol/openid-connect/certs',
                    'issuer': upstream_base,
                    'useJwksUrl': 'true',
                    'validateSignature': 'true',
                    'defaultScope': 'openid profile email',
                    'syncMode': 'IMPORT',
                },
            }
        ],
        'identityProviderMappers': [
            {
                'name': 'login provider',
                'identityProviderAlias': 'enterprise_sso',
                'identityProviderMapper': 'hardcoded-attribute-idp-mapper',
                'config': {
                    'attribute': 'identity_provider',
                    'attribute.value': 'enterprise_sso',
                    'syncMode': 'INHERIT',
                },
            }
        ],
    }
    for name, realm in [('enterprise', enterprise), ('synthetic-upstream', upstream)]:
        (imports / f'{name}-realm.json').write_text(json.dumps(realm))
    envfile = WORK / 'keycloak.env'
    envfile.write_text(
        'KC_BOOTSTRAP_ADMIN_USERNAME=admin\nKC_BOOTSTRAP_ADMIN_PASSWORD='
        + state['admin_password']
        + '\n'
    )
    envfile.chmod(0o600)
    subprocess.run(
        [
            'docker',
            'exec',
            'oh-auth-core-pg-ca5a3a34ca4c',
            'createdb',
            '-U',
            'postgres',
            '-T',
            'auth_historic',
            state['database'],
        ],
        check=True,
    )
    subprocess.run(
        [
            'docker',
            'run',
            '-d',
            '--name',
            state['redis_name'],
            '-p',
            f'127.0.0.1:{state["redis_port"]}:6379',
            'redis:7-alpine',
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            'docker',
            'run',
            '-d',
            '--name',
            state['kc_name'],
            '--env-file',
            str(envfile),
            '-p',
            f'127.0.0.1:{state["kc_port"]}:{state["kc_port"]}',
            '-v',
            f'{imports}:/opt/keycloak/data/import:ro',
            'quay.io/keycloak/keycloak:26.5.5',
            'start-dev',
            '--import-realm',
            '--http-port',
            str(state['kc_port']),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            'openssl',
            'req',
            '-x509',
            '-newkey',
            'rsa:2048',
            '-nodes',
            '-days',
            '2',
            '-keyout',
            str(WORK / 'localhost.key'),
            '-out',
            str(WORK / 'localhost.crt'),
            '-subj',
            '/CN=localhost',
            '-addext',
            'subjectAltName=DNS:localhost,IP:127.0.0.1',
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in state.items()
                if key.endswith(('_name', '_port', '_url')) or key == 'database'
            }
        )
    )


if __name__ == '__main__':
    if '--setup' in sys.argv:
        setup()
    else:
        state, env = environment()
        if '--migrate' in sys.argv:
            raise SystemExit(
                subprocess.call(
                    [str(ROOT / '.venv/bin/alembic'), 'upgrade', 'head'],
                    cwd=ROOT,
                    env=env,
                )
            )
        if '--script' in sys.argv:
            args = [
                str(ROOT / '.venv/bin/python'),
                *sys.argv[sys.argv.index('--script') + 1 :],
            ]
        else:
            args = [
                str(ROOT / '.venv/bin/python'),
                '-m',
                'uvicorn',
                'saas_server:app',
                '--host',
                '127.0.0.1',
                '--port',
                str(state['app_port']),
                '--ssl-keyfile',
                str(WORK / 'localhost.key'),
                '--ssl-certfile',
                str(WORK / 'localhost.crt'),
            ]
        os.chdir(ROOT)
        os.execve(args[0], args, env)
