"""OpenHands-issued OAuth grants for the external MCP endpoint.

Any registered MCP client (ChatGPT, an IDE, another agent tool) signs the user
in through the normal OpenHands browser login and receives a short-lived,
org-bound grant that is only valid on this endpoint.
"""

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from redis.asyncio import Redis

from server.auth.org_access import OrgNotUsableError, assert_org_usable_for_product
from server.services.feature_flag_service import feature_flag_service
from storage.org_member_store import OrgMemberStore
from storage.user_store import UserStore

FEATURE_FLAG = 'ENABLE_EXTERNAL_MCP'
SCOPE = 'tasks:read'
CODE_TTL = 300
ACCESS_TTL = 900
REFRESH_TTL = 30 * 86400
_LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _key(kind: str, value: str) -> str:
    return f'external-mcp-oauth:{kind}:{_digest(value)}'


async def feature_enabled(user_id: str, org_id: str) -> bool:
    """Whether the external MCP endpoint is switched on for this user and org."""
    return await feature_flag_service.resolve(
        FEATURE_FLAG, user_id=user_id, org_id=org_id
    )


@dataclass(frozen=True)
class RegisteredClient:
    """An MCP client an administrator has registered for this endpoint.

    A client without a secret is a public client (a desktop or CLI tool); it
    is protected by PKCE and its exact redirect URIs.
    """

    client_id: str
    name: str
    redirect_uris: tuple[str, ...]
    client_secret: str | None = None


def _valid_redirect_uri(uri: object) -> bool:
    if not isinstance(uri, str):
        return False
    parts = urlsplit(uri)
    if not parts.hostname or parts.fragment:
        return False
    if parts.scheme == 'https':
        return True
    return parts.scheme == 'http' and parts.hostname in _LOOPBACK_HOSTS


def parse_registered_clients(raw: str) -> dict[str, RegisteredClient]:
    """Parse the configured client list, a JSON array of objects.

    Each object has ``client_id``, ``name``, ``redirect_uris`` and an optional
    ``client_secret``. Redirect URIs must be HTTPS, or HTTP on a loopback
    host for tools that listen locally.
    """
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('Registered MCP clients must be a JSON array') from exc
    if not isinstance(entries, list):
        raise ValueError('Registered MCP clients must be a JSON array')
    clients: dict[str, RegisteredClient] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Each registered MCP client must be an object')
        client_id = entry.get('client_id')
        name = entry.get('name')
        redirect_uris = entry.get('redirect_uris')
        secret = entry.get('client_secret')
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError('A registered MCP client needs a client_id')
        if client_id in clients:
            raise ValueError(f'Duplicate MCP client_id: {client_id}')
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'MCP client {client_id} needs a name')
        if (
            not isinstance(redirect_uris, list)
            or not redirect_uris
            or not all(_valid_redirect_uri(uri) for uri in redirect_uris)
        ):
            raise ValueError(
                f'MCP client {client_id} needs HTTPS or loopback redirect_uris'
            )
        if secret is not None and (not isinstance(secret, str) or not secret):
            raise ValueError(f'MCP client {client_id} has an invalid client_secret')
        clients[client_id] = RegisteredClient(
            client_id=client_id,
            name=name.strip(),
            redirect_uris=tuple(redirect_uris),
            client_secret=secret,
        )
    return clients


class ExternalMcpOAuth(OAuthProvider):
    """Pre-registered MCP clients; Redis-backed, revocable OAuth grants."""

    def __init__(
        self,
        *,
        base_url: str,
        clients: dict[str, RegisteredClient],
        redis: Redis,
    ) -> None:
        super().__init__(
            base_url=base_url,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=False, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.clients = clients
        self.redis = redis
        self.resource = f'{base_url.rstrip("/")}/mcp'

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        client = self.clients.get(client_id)
        if client is None:
            return None
        return OAuthClientInformationFull(
            client_id=client.client_id,
            client_name=client.name,
            redirect_uris=[AnyUrl(uri) for uri in client.redirect_uris],
            token_endpoint_auth_method=(
                'client_secret_post' if client.client_secret else 'none'
            ),
            client_secret=client.client_secret,
            grant_types=['authorization_code', 'refresh_token'],
            response_types=['code'],
            scope=SCOPE,
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        raise ValueError('Dynamic client registration is disabled')

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource != self.resource:
            raise AuthorizeError('invalid_request', 'Unexpected resource')
        if set(params.scopes or []) != {SCOPE}:
            raise AuthorizeError('invalid_scope')
        handle = secrets.token_urlsafe(32)
        await self.redis.set(
            _key('request', handle),
            json.dumps(
                {
                    'client_id': client.client_id,
                    'redirect_uri': str(params.redirect_uri),
                    'redirect_uri_provided_explicitly': params.redirect_uri_provided_explicitly,
                    'code_challenge': params.code_challenge,
                    'state': params.state,
                    'resource': params.resource,
                }
            ),
            ex=CODE_TTL,
        )
        return f'{self.base_url}/consent?{urlencode({"request": handle})}'

    async def consent_request(self, handle: str) -> dict | None:
        raw = await self.redis.get(_key('request', handle))
        if not raw:
            return None
        request = json.loads(raw)
        return request if request['client_id'] in self.clients else None

    async def approve(self, handle: str, user_id: str, org_id: str) -> str | None:
        raw = await self.redis.getdel(_key('request', handle))
        if not raw:
            return None
        request = json.loads(raw)
        code = secrets.token_urlsafe(32)
        await self.redis.set(
            _key('code', code),
            json.dumps(
                {
                    **request,
                    'user_id': user_id,
                    'org_id': org_id,
                    'issued_at': time.time(),
                    'grant_id': secrets.token_urlsafe(24),
                }
            ),
            ex=CODE_TTL,
        )
        query = {'code': code, 'iss': str(self.issuer_url)}
        if request['state'] is not None:
            query['state'] = request['state']
        separator = '&' if '?' in request['redirect_uri'] else '?'
        return request['redirect_uri'] + separator + urlencode(query)

    async def deny(self, handle: str) -> str | None:
        raw = await self.redis.getdel(_key('request', handle))
        if not raw:
            return None
        request = json.loads(raw)
        query = {'error': 'access_denied', 'iss': str(self.issuer_url)}
        if request['state'] is not None:
            query['state'] = request['state']
        separator = '&' if '?' in request['redirect_uri'] else '?'
        return request['redirect_uri'] + separator + urlencode(query)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        raw = await self.redis.get(_key('code', authorization_code))
        if not raw:
            return None
        record = json.loads(raw)
        if (
            record['client_id'] != client.client_id
            or record['resource'] != self.resource
        ):
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=[SCOPE],
            expires_at=time.time() + CODE_TTL,
            client_id=record['client_id'],
            code_challenge=record['code_challenge'],
            redirect_uri=AnyUrl(record['redirect_uri']),
            redirect_uri_provided_explicitly=record['redirect_uri_provided_explicitly'],
            resource=self.resource,
            subject=record['user_id'],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        raw = await self.redis.getdel(_key('code', authorization_code.code))
        if not raw:
            raise TokenError('invalid_grant')
        record = json.loads(raw)
        if record['client_id'] != client.client_id:
            raise TokenError('invalid_grant')
        return await self._issue_tokens(record)

    async def _issue_tokens(self, record: dict) -> OAuthToken:
        if not await self._active_principal(record):
            raise TokenError('invalid_grant')
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(48)
        await self.redis.set(_key('access', access), json.dumps(record), ex=ACCESS_TTL)
        await self.redis.set(
            _key('refresh', refresh), json.dumps(record), ex=REFRESH_TTL
        )
        return OAuthToken(
            access_token=access,
            token_type='Bearer',
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=SCOPE,
        )

    async def _active_principal(self, record: dict) -> bool:
        try:
            # A client removed from the configuration loses its grants.
            if record['client_id'] not in self.clients:
                return False
            if await self.redis.exists(_key('revoked', record['grant_id'])):
                return False
            if not await feature_enabled(record['user_id'], record['org_id']):
                return False
            user = await UserStore.get_user_auth_state(record['user_id'])
            if user is None or user.is_disabled or user.deletion_pending:
                return False
            revoked = user.credentials_revoked_at
            if revoked and revoked.timestamp() >= record.get('issued_at', 0):
                return False
            await assert_org_usable_for_product(
                UUID(record['org_id']), user_id=record['user_id']
            )
            member = await OrgMemberStore.get_org_member(
                UUID(record['org_id']), UUID(record['user_id'])
            )
            return member is not None and member.status != 'inactive'
        except (ValueError, KeyError, OrgNotUsableError):
            return False

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        raw = await self.redis.get(_key('refresh', refresh_token))
        if not raw:
            return None
        record = json.loads(raw)
        if record['client_id'] != client.client_id or not await self._active_principal(
            record
        ):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record['client_id'],
            scopes=[SCOPE],
            subject=record['user_id'],
            expires_at=int(time.time()) + REFRESH_TTL,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if set(scopes) != {SCOPE}:
            raise TokenError('invalid_scope')
        raw = await self.redis.getdel(_key('refresh', refresh_token.token))
        if not raw:
            raise TokenError('invalid_grant')
        record = json.loads(raw)
        if record['client_id'] != client.client_id:
            raise TokenError('invalid_grant')
        return await self._issue_tokens(record)

    async def load_access_token(self, token: str) -> AccessToken | None:
        raw = await self.redis.get(_key('access', token))
        if not raw:
            return None
        record = json.loads(raw)
        if record['resource'] != self.resource:
            return None
        if not await self._active_principal(record):
            return None
        return AccessToken(
            token=token,
            client_id=record['client_id'],
            scopes=[SCOPE],
            expires_at=int(time.time()) + ACCESS_TTL,
            resource=self.resource,
            subject=record['user_id'],
            claims={'org_id': record['org_id']},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        kind = 'refresh' if isinstance(token, RefreshToken) else 'access'
        raw = await self.redis.getdel(_key(kind, token.token))
        if raw:
            record = json.loads(raw)
            await self.redis.set(
                _key('revoked', record['grant_id']), '1', ex=REFRESH_TTL
            )
