#!/usr/bin/env python3
"""Stateless mock OIDC identity provider for testing token expiry.

The authorize page lets you pick the email, whether it is verified, and how
long the access and refresh tokens live. Every value is carried inside signed
JWTs (authorization code, access token, refresh token), so nothing is stored
server side. The RSA signing key is generated at startup, so restarting the
server invalidates every token it issued.

The refresh token expiry is absolute: refreshing issues a new access token but
the new refresh token keeps the original expiry, so the session always ends
``refresh_token_expires_in`` seconds after login.

Client credentials are not checked; any client_id / client_secret is accepted.

Usage:
    uv run python scripts/mock_oidc_idp.py
    uv run python scripts/mock_oidc_idp.py --issuer http://host.docker.internal:3060

Endpoints (relative to the issuer):
    GET  /.well-known/openid-configuration
    GET  /jwks
    GET  /authorize            login form
    POST /authorize            issues an authorization code and redirects
    POST /token                authorization_code and refresh_token grants
    GET  /userinfo             (POST also accepted)
"""

import argparse
import base64
import hashlib
import html
import logging
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jwt.algorithms import RSAAlgorithm

DEFAULT_PORT = 3060
CODE_TTL_SECONDS = 120
ALGORITHM = 'RS256'

logger = logging.getLogger('mock_oidc_idp')


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    logger.warning('%s: %s', error, description)
    return JSONResponse(
        {'error': error, 'error_description': description},
        status_code=status_code,
        headers={'Cache-Control': 'no-store'},
    )


def _subject_for(email: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'mock-oidc-idp:{email.lower()}'))


def _pkce_matches(verifier: str, challenge: str, method: str) -> bool:
    if method == 'plain':
        return secrets.compare_digest(verifier, challenge)
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
    return secrets.compare_digest(expected, challenge)


AUTHORIZE_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Mock OIDC IdP</title>
  <style>
    body {{ font-family: sans-serif; max-width: 28rem; margin: 3rem auto; }}
    label {{ display: block; margin: 1rem 0 0.25rem; }}
    input[type=email], input[type=number] {{ width: 100%; padding: 0.4rem; box-sizing: border-box; }}
    .inline {{ display: flex; gap: 0.5rem; align-items: center; }}
    button {{ margin-top: 1.5rem; padding: 0.5rem 1.5rem; }}
    .client {{ color: #666; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>Mock OIDC IdP</h1>
  <p class="client">Signing in to <code>{client_id}</code></p>
  <form id="login" method="post" action="authorize">
    {hidden_fields}
    <label for="email">Email</label>
    <input id="email" name="email" type="email" required>
    <label class="inline">
      <input id="email_verified" name="email_verified" type="checkbox" value="true">
      Email verified
    </label>
    <label for="access_token_expires_in">Access token expires in (seconds)</label>
    <input id="access_token_expires_in" name="access_token_expires_in" type="number" min="1" required>
    <label for="refresh_token_expires_in">Refresh token expires in (seconds)</label>
    <input id="refresh_token_expires_in" name="refresh_token_expires_in" type="number" min="1" required>
    <button type="submit">Sign in</button>
  </form>
  <script>
    const STORAGE_KEY = 'mock-oidc-idp';
    const DEFAULTS = {{
      email: 'test@example.com',
      email_verified: true,
      access_token_expires_in: 300,
      refresh_token_expires_in: 1800,
    }};
    const form = document.getElementById('login');
    let saved = {{}};
    try {{ saved = JSON.parse(localStorage.getItem(STORAGE_KEY)) || {{}}; }} catch (e) {{}}
    const values = {{ ...DEFAULTS, ...saved }};
    form.email.value = values.email;
    form.email_verified.checked = values.email_verified;
    form.access_token_expires_in.value = values.access_token_expires_in;
    form.refresh_token_expires_in.value = values.refresh_token_expires_in;
    form.addEventListener('submit', () => {{
      localStorage.setItem(STORAGE_KEY, JSON.stringify({{
        email: form.email.value,
        email_verified: form.email_verified.checked,
        access_token_expires_in: Number(form.access_token_expires_in.value),
        refresh_token_expires_in: Number(form.refresh_token_expires_in.value),
      }}));
    }});
  </script>
</body>
</html>
"""

AUTHORIZE_PASSTHROUGH_PARAMS = (
    'client_id',
    'redirect_uri',
    'response_type',
    'scope',
    'state',
    'nonce',
    'code_challenge',
    'code_challenge_method',
)


def create_app(issuer: str) -> FastAPI:
    issuer = issuer.rstrip('/')
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    kid = secrets.token_hex(8)
    jwk = {
        **RSAAlgorithm.to_jwk(public_key, as_dict=True),
        'kid': kid,
        'use': 'sig',
        'alg': ALGORITHM,
    }

    def sign(claims: dict[str, Any]) -> str:
        return jwt.encode(
            claims, private_key, algorithm=ALGORITHM, headers={'kid': kid}
        )

    def verify(token: str, token_use: str) -> dict[str, Any]:
        claims = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            issuer=issuer,
            options={'verify_aud': False, 'require': ['exp', 'iat', 'iss']},
        )
        if claims.get('token_use') != token_use:
            raise jwt.InvalidTokenError(f'expected a {token_use} token')
        return claims

    def issue_tokens(session: dict[str, Any], refresh_exp: int) -> dict[str, Any]:
        now = int(time.time())
        access_ttl = session['access_token_expires_in']
        sub = _subject_for(session['email'])
        identity = {
            'iss': issuer,
            'sub': sub,
            'iat': now,
            'email': session['email'],
            'email_verified': session['email_verified'],
        }
        access_token = sign(
            {
                **identity,
                'aud': session['client_id'],
                'exp': now + access_ttl,
                'jti': secrets.token_hex(8),
                'token_use': 'access',
                'scope': session['scope'],
            }
        )
        id_claims = {
            **identity,
            'aud': session['client_id'],
            'azp': session['client_id'],
            'exp': now + access_ttl,
            'auth_time': session['auth_time'],
            'preferred_username': session['email'],
            'name': session['email'].split('@')[0],
        }
        if session.get('nonce'):
            id_claims['nonce'] = session['nonce']
        refresh_token = sign(
            {
                'iss': issuer,
                'sub': sub,
                'iat': now,
                'exp': refresh_exp,
                'jti': secrets.token_hex(8),
                'token_use': 'refresh',
                'session': session,
            }
        )
        logger.info(
            'Issued tokens for %s: access expires in %ss, refresh expires in %ss',
            session['email'],
            access_ttl,
            refresh_exp - now,
        )
        return {
            'access_token': access_token,
            'token_type': 'Bearer',
            'expires_in': access_ttl,
            'refresh_token': refresh_token,
            'refresh_expires_in': refresh_exp - now,
            'id_token': sign(id_claims),
            'scope': session['scope'],
        }

    app = FastAPI(title='Mock OIDC IdP')

    @app.get('/.well-known/openid-configuration')
    def discovery() -> dict[str, Any]:
        return {
            'issuer': issuer,
            'authorization_endpoint': f'{issuer}/authorize',
            'token_endpoint': f'{issuer}/token',
            'userinfo_endpoint': f'{issuer}/userinfo',
            'jwks_uri': f'{issuer}/jwks',
            'response_types_supported': ['code'],
            'grant_types_supported': ['authorization_code', 'refresh_token'],
            'subject_types_supported': ['public'],
            'id_token_signing_alg_values_supported': [ALGORITHM],
            'scopes_supported': ['openid', 'email', 'profile', 'offline_access'],
            'token_endpoint_auth_methods_supported': [
                'client_secret_basic',
                'client_secret_post',
                'none',
            ],
            'claims_supported': [
                'sub',
                'email',
                'email_verified',
                'name',
                'preferred_username',
            ],
            'code_challenge_methods_supported': ['S256', 'plain'],
        }

    @app.get('/jwks')
    def jwks() -> dict[str, Any]:
        return {'keys': [jwk]}

    @app.get('/authorize', response_class=HTMLResponse)
    def authorize_page(request: Request) -> HTMLResponse:
        params = request.query_params
        if params.get('response_type') != 'code':
            return HTMLResponse('Only response_type=code is supported', 400)
        if not params.get('client_id') or not params.get('redirect_uri'):
            return HTMLResponse('client_id and redirect_uri are required', 400)
        hidden_fields = '\n    '.join(
            f'<input type="hidden" name="{name}" value="{html.escape(params[name])}">'
            for name in AUTHORIZE_PASSTHROUGH_PARAMS
            if name in params
        )
        return HTMLResponse(
            AUTHORIZE_PAGE.format(
                client_id=html.escape(params['client_id']),
                hidden_fields=hidden_fields,
            )
        )

    @app.post('/authorize')
    def authorize_submit(
        client_id: str = Form(),
        redirect_uri: str = Form(),
        email: str = Form(),
        access_token_expires_in: int = Form(gt=0),
        refresh_token_expires_in: int = Form(gt=0),
        email_verified: bool = Form(False),
        scope: str = Form('openid'),
        state: str | None = Form(None),
        nonce: str | None = Form(None),
        code_challenge: str | None = Form(None),
        code_challenge_method: str = Form('plain'),
    ) -> RedirectResponse:
        now = int(time.time())
        code = sign(
            {
                'iss': issuer,
                'iat': now,
                'exp': now + CODE_TTL_SECONDS,
                'token_use': 'code',
                'redirect_uri': redirect_uri,
                'code_challenge': code_challenge,
                'code_challenge_method': code_challenge_method,
                'session': {
                    'client_id': client_id,
                    'email': email,
                    'email_verified': email_verified,
                    'access_token_expires_in': access_token_expires_in,
                    'refresh_token_expires_in': refresh_token_expires_in,
                    'scope': scope,
                    'nonce': nonce,
                    'auth_time': now,
                },
            }
        )
        query = {'code': code}
        if state is not None:
            query['state'] = state
        separator = '&' if '?' in redirect_uri else '?'
        return RedirectResponse(f'{redirect_uri}{separator}{urlencode(query)}', 302)

    @app.post('/token')
    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        grant_type = form.get('grant_type')

        if grant_type == 'authorization_code':
            try:
                code = verify(str(form.get('code', '')), 'code')
            except jwt.InvalidTokenError as e:
                return _oauth_error('invalid_grant', f'Invalid code: {e}')
            if form.get('redirect_uri') != code['redirect_uri']:
                return _oauth_error('invalid_grant', 'redirect_uri mismatch')
            if code['code_challenge'] and not _pkce_matches(
                str(form.get('code_verifier', '')),
                code['code_challenge'],
                code['code_challenge_method'],
            ):
                return _oauth_error('invalid_grant', 'PKCE verification failed')
            session = code['session']
            refresh_exp = int(time.time()) + session['refresh_token_expires_in']
            return JSONResponse(
                issue_tokens(session, refresh_exp),
                headers={'Cache-Control': 'no-store'},
            )

        if grant_type == 'refresh_token':
            try:
                refresh = verify(str(form.get('refresh_token', '')), 'refresh')
            except jwt.ExpiredSignatureError:
                return _oauth_error('invalid_grant', 'Refresh token expired')
            except jwt.InvalidTokenError as e:
                return _oauth_error('invalid_grant', f'Invalid refresh token: {e}')
            return JSONResponse(
                issue_tokens(refresh['session'], refresh['exp']),
                headers={'Cache-Control': 'no-store'},
            )

        return _oauth_error(
            'unsupported_grant_type', f'Unsupported grant_type: {grant_type}'
        )

    @app.api_route('/userinfo', methods=['GET', 'POST'])
    def userinfo(request: Request) -> JSONResponse:
        scheme, _, access_token = request.headers.get('Authorization', '').partition(
            ' '
        )
        if scheme.lower() != 'bearer' or not access_token:
            return _oauth_error('invalid_request', 'Missing bearer token', 401)
        try:
            claims = verify(access_token, 'access')
        except jwt.InvalidTokenError as e:
            logger.warning('Rejected userinfo request: %s', e)
            return JSONResponse(
                {'error': 'invalid_token', 'error_description': str(e)},
                status_code=401,
                headers={
                    'WWW-Authenticate': f'Bearer error="invalid_token", error_description="{e}"'
                },
            )
        email = claims['email']
        return JSONResponse(
            {
                'sub': claims['sub'],
                'email': email,
                'email_verified': claims['email_verified'],
                'preferred_username': email,
                'name': email.split('@')[0],
            }
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument(
        '--issuer',
        help='Public base URL of this server (default: http://localhost:<port>). '
        'Must match the URL the relying party uses to reach it.',
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s'
    )
    issuer = args.issuer or f'http://localhost:{args.port}'
    logger.info('Issuer: %s', issuer)
    logger.info('Discovery: %s/.well-known/openid-configuration', issuer.rstrip('/'))
    uvicorn.run(create_app(issuer), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
