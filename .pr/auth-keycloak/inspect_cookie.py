import json
from pathlib import Path
from uuid import UUID

import jwt

from storage.encrypt_utils import get_jwt_service

work = Path(__file__).resolve().parent
cookies = {
    cookie['name']: cookie['value']
    for cookie in json.loads((work / 'pre-request-state.json').read_text())['cookies']
}
signed = ''.join(
    cookies.get('keycloak_auth' + (f'_{i}' if i else ''), '') for i in range(8)
)
for phase in ('signature', 'access', 'subject'):
    try:
        if phase == 'signature':
            decoded = get_jwt_service().verify_jws_token(signed)
            print('signature passed', sorted(decoded))
        elif phase == 'access':
            access = jwt.decode(
                decoded['access_token'], options={'verify_signature': False}
            )
            print('access decoded', sorted(access))
        else:
            print('subject parsed', UUID(access['sub']))
    except Exception as exc:
        print(
            phase,
            type(exc).__name__,
            str(exc),
            'cause',
            type(exc.__cause__).__name__,
            str(exc.__cause__),
        )
        if phase == 'signature':
            decoded = jwt.decode(signed, options={'verify_signature': False})
