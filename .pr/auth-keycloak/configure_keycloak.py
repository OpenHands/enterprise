"""Configure only the disposable synthetic Keycloak realms."""

import json
import sys
from pathlib import Path

import httpx

WORK = Path(__file__).resolve().parent
state = json.loads((WORK / 'state.json').read_text())
client = httpx.Client(base_url=state['kc_url'], timeout=15)
response = client.post(
    '/realms/master/protocol/openid-connect/token',
    data={
        'grant_type': 'password',
        'client_id': 'admin-cli',
        'username': 'admin',
        'password': state['admin_password'],
    },
)
response.raise_for_status()
client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']


def api(method, path, **kwargs):
    response = client.request(method, '/admin/realms/' + path, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


if sys.argv[1] == 'basic':
    app = api('GET', 'enterprise/clients', params={'clientId': 'openhands'})[0]
    basic = next(
        scope
        for scope in api('GET', 'enterprise/client-scopes')
        if scope['name'] == 'basic'
    )
    api('PUT', f'enterprise/clients/{app["id"]}/default-client-scopes/{basic["id"]}')
    print(json.dumps({'default_basic_client_scope': 'enabled'}))
elif sys.argv[1] == 'sessions':
    users = api(
        'GET',
        'enterprise/users',
        params={'email': state['legacy_email'], 'exact': 'true'},
    )
    sessions = api('GET', f'enterprise/users/{users[0]["id"]}/sessions')
    print(json.dumps({'legacy_online_sessions': len(sessions)}))
elif sys.argv[1] == 'mapper-types':
    types = api(
        'GET', 'enterprise/identity-provider/instances/enterprise_sso/mapper-types'
    )
    print(
        json.dumps(
            {key: value for key, value in types.items() if 'hardcode' in key.lower()},
            indent=2,
        )
    )
elif sys.argv[1] == 'oidc-mapper':
    types = api(
        'GET', 'enterprise/identity-provider/instances/enterprise_sso/mapper-types'
    )
    chosen = next(
        key for key, value in types.items() if value['name'] == 'Hardcoded Attribute'
    )
    mapper = api(
        'GET', 'enterprise/identity-provider/instances/enterprise_sso/mappers'
    )[0]
    mapper['identityProviderMapper'] = chosen
    api(
        'PUT',
        f'enterprise/identity-provider/instances/enterprise_sso/mappers/{mapper["id"]}',
        json=mapper,
    )
    profile = api('GET', 'enterprise/users/profile')
    if not any(item['name'] == 'identity_provider' for item in profile['attributes']):
        profile['attributes'].append(
            {
                'name': 'identity_provider',
                'displayName': 'Identity provider',
                'permissions': {'view': ['admin'], 'edit': ['admin']},
                'multivalued': False,
            }
        )
        api('PUT', 'enterprise/users/profile', json=profile)
    users = api(
        'GET',
        'enterprise/users',
        params={'email': state['broker_email'], 'exact': 'true'},
    )
    for user in users:
        user.setdefault('attributes', {})['identity_provider'] = ['enterprise_sso']
        api('PUT', f'enterprise/users/{user["id"]}', json=user)
    print(json.dumps({'hardcoded_mapper_provider': chosen}))
elif sys.argv[1] == 'broker-profile':
    users = api(
        'GET',
        'enterprise/users',
        params={'email': state['broker_email'], 'exact': 'true'},
    )
    assert len(users) == 1
    user = users[0]
    identities = api('GET', f'enterprise/users/{user["id"]}/federated-identity')
    print(
        json.dumps(
            {
                'broker_user_id': user['id'],
                'email_verified': user['emailVerified'],
                'identity_provider_attribute': user.get('attributes', {}).get(
                    'identity_provider'
                ),
                'federated_provider': [item['identityProvider'] for item in identities],
            }
        )
    )
elif sys.argv[1] == 'saml':
    state['oidc_broker_email'] = state['broker_email']
    state['broker_email'] = 'saml@keycloak-qa.example'
    (WORK / 'state.json').write_text(json.dumps(state, indent=2))
    sp_entity = state['kc_url'] + '/realms/enterprise'
    upstream = state['kc_url'] + '/realms/synthetic-upstream'
    endpoint = sp_entity + '/broker/enterprise_sso/endpoint'
    keys = api('GET', 'synthetic-upstream/keys')
    signing_certificate = next(
        key['certificate']
        for key in keys['keys']
        if key.get('algorithm') == 'RS256' and key['status'] == 'ACTIVE'
    )
    saml_client = {
        'clientId': sp_entity,
        'protocol': 'saml',
        'enabled': True,
        'redirectUris': [endpoint],
        'attributes': {
            'saml_assertion_consumer_url_post': endpoint,
            'saml.force.post.binding': 'true',
            'saml.server.signature': 'true',
            'saml.assertion.signature': 'true',
            'saml.client.signature': 'false',
            'saml.authnstatement': 'true',
            'saml.signature.algorithm': 'RSA_SHA256',
            'saml_name_id_format': 'email',
            'saml.force.name.id.format': 'true',
        },
        'protocolMappers': [
            {
                'name': attribute,
                'protocol': 'saml',
                'protocolMapper': 'saml-user-property-mapper',
                'config': {
                    'user.attribute': attribute,
                    'attribute.name': claim,
                    'attribute.nameformat': 'Basic',
                    'friendly.name': claim,
                },
            }
            for attribute, claim in [
                ('email', 'email'),
                ('firstName', 'firstName'),
                ('lastName', 'lastName'),
            ]
        ],
    }
    api('POST', 'synthetic-upstream/clients', json=saml_client)
    api(
        'POST',
        'synthetic-upstream/users',
        json={
            'username': state['broker_email'],
            'email': state['broker_email'],
            'emailVerified': True,
            'enabled': True,
            'firstName': 'Synthetic',
            'lastName': 'SAML',
            'credentials': [
                {'type': 'password', 'value': state['password'], 'temporary': False}
            ],
        },
    )
    api('DELETE', 'enterprise/identity-provider/instances/enterprise_sso')
    api(
        'POST',
        'enterprise/identity-provider/instances',
        json={
            'alias': 'enterprise_sso',
            'providerId': 'saml',
            'enabled': True,
            'trustEmail': True,
            'firstBrokerLoginFlowAlias': 'first broker login',
            'config': {
                'entityId': sp_entity,
                'idpEntityId': upstream,
                'singleSignOnServiceUrl': upstream + '/protocol/saml',
                'signingCertificate': signing_certificate,
                'validateSignature': 'true',
                'postBindingResponse': 'true',
                'postBindingAuthnRequest': 'true',
                'wantAuthnRequestsSigned': 'false',
                'nameIDPolicyFormat': 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress',
                'principalType': 'SUBJECT',
                'syncMode': 'IMPORT',
            },
        },
    )
    api(
        'POST',
        'enterprise/identity-provider/instances/enterprise_sso/mappers',
        json={
            'name': 'login provider',
            'identityProviderAlias': 'enterprise_sso',
            'identityProviderMapper': 'hardcoded-attribute-idp-mapper',
            'config': {
                'attribute': 'identity_provider',
                'attribute.value': 'enterprise_sso:saml',
                'syncMode': 'INHERIT',
            },
        },
    )
    for attribute in ('email', 'firstName', 'lastName'):
        api(
            'POST',
            'enterprise/identity-provider/instances/enterprise_sso/mappers',
            json={
                'name': attribute,
                'identityProviderAlias': 'enterprise_sso',
                'identityProviderMapper': 'saml-user-attribute-idp-mapper',
                'config': {
                    'attribute.name': attribute,
                    'attribute.friendly.name': attribute,
                    'user.attribute': attribute,
                    'syncMode': 'INHERIT',
                },
            },
        )
    print(
        json.dumps(
            {
                'selected_provider': 'saml',
                'signed_assertion_validation': True,
                'alias': 'enterprise_sso',
            }
        )
    )
else:
    raise SystemExit('Unknown phase')
