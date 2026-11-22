"""SCIM 2.0 users-only prototype (``/scim/v2``).

Registered only when ``SCIM_ENABLED`` is set (``saas_server.py``). A request
authenticates with a dedicated ``scim_credential`` token, which names exactly
one organization; every lookup and write below is confined to it, so another
organization's resource id is a ``404`` here.

Supported: ``ServiceProviderConfig``, ``ResourceTypes``, ``Schemas``, and on
``/Users`` list (``userName eq`` / ``externalId eq``, ``startIndex`` and
``count``), create, get, ``PUT`` and a ``PATCH`` that replaces ``active`` with
a JSON boolean. Groups, ``DELETE``, bulk, sorting, ETags, password changes and
any other filter or patch are not implemented.

What the writes do to accounts and memberships is decided in
``server.services.scim_user_service``.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from server.services.scim_user_service import (
    ScimError,
    ScimUserAttributes,
    ScimUserService,
)
from storage.auth_provider_details import PasswordProviderDetails
from storage.auth_provider_store import AuthProviderStore
from storage.default_org_service import is_personal_workspace_org
from storage.org_store import OrgStore
from storage.scim import ScimUser
from storage.scim_store import ScimStore

SCIM_MEDIA_TYPE = 'application/scim+json'
USER_SCHEMA = 'urn:ietf:params:scim:schemas:core:2.0:User'
LIST_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:ListResponse'
PATCH_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:PatchOp'
ERROR_SCHEMA = 'urn:ietf:params:scim:api:messages:2.0:Error'
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 100


class ScimResponse(JSONResponse):
    media_type = SCIM_MEDIA_TYPE


def scim_error_response(error: ScimError) -> ScimResponse:
    body: dict[str, Any] = {
        'schemas': [ERROR_SCHEMA],
        'status': str(error.status),
        'detail': error.detail,
    }
    if error.scim_type:
        body['scimType'] = error.scim_type
    headers = {'WWW-Authenticate': 'Bearer'} if error.status == 401 else None
    return ScimResponse(body, status_code=error.status, headers=headers)


def _invalid(detail: str, scim_type: str = 'invalidValue') -> ScimError:
    return ScimError(400, detail, scim_type)


class _ScimRoute(APIRoute):
    """Answer every failure on these routes with a SCIM error envelope."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def scim_handler(request: Request):
            try:
                return await handler(request)
            except ScimError as error:
                return scim_error_response(error)
            except RequestValidationError:
                return scim_error_response(_invalid('Malformed request'))

        return scim_handler


scim_router = APIRouter(prefix='/scim/v2', tags=['SCIM'], route_class=_ScimRoute)


async def scim_service(request: Request) -> ScimUserService:
    """Authenticate the SCIM token and scope the request to its organization."""
    scheme, _, token = request.headers.get('Authorization', '').partition(' ')
    token = token.strip()
    if scheme.lower() != 'bearer' or not token:
        raise ScimError(401, 'A SCIM bearer token is required')
    credential = await ScimStore.get_credential_by_token(token)
    if credential is None:
        raise ScimError(401, 'Invalid SCIM token')
    org = await OrgStore.get_org_by_id(credential.org_id)
    if org is None or is_personal_workspace_org(org):
        raise ScimError(403, 'This credential has no team organization')
    provider = await AuthProviderStore().get_by_id(credential.auth_provider_id)
    if (
        provider is None
        or not provider.enabled
        or not provider.is_idp
        or isinstance(provider.details, PasswordProviderDetails)
    ):
        raise ScimError(
            403, 'This credential is not bound to an enabled corporate SSO provider'
        )
    return ScimUserService(org.id, provider)


SCIM_SERVICE = Depends(scim_service)


class _Name(BaseModel):
    model_config = ConfigDict(extra='ignore')
    givenName: str | None = Field(default=None, max_length=255)
    familyName: str | None = Field(default=None, max_length=255)


class _Email(BaseModel):
    model_config = ConfigDict(extra='ignore')
    value: str = Field(max_length=255)
    primary: bool = False


class _UserPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')
    userName: str = Field(min_length=1, max_length=255)
    externalId: str | None = Field(default=None, min_length=1, max_length=255)
    name: _Name | None = None
    displayName: str | None = Field(default=None, max_length=255)
    emails: list[_Email] = Field(default_factory=list)
    active: StrictBool = True


class _PatchOperation(BaseModel):
    model_config = ConfigDict(extra='ignore')
    op: str
    path: str | None = None
    value: Any = None


class _PatchPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')
    Operations: list[_PatchOperation] = Field(min_length=1)


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise _invalid('Request body is not valid JSON', 'invalidSyntax') from exc
    if not isinstance(body, dict):
        raise _invalid('Request body must be a JSON object', 'invalidSyntax')
    return body


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    local, at, domain = email.partition('@')
    if not at or not local or '.' not in domain or '@' in domain or ' ' in email:
        raise _invalid('A valid email address is required')
    return email


async def _user_attributes(request: Request) -> ScimUserAttributes:
    try:
        payload = _UserPayload.model_validate(await _json_object(request))
    except ValidationError as exc:
        raise _invalid(_first_problem(exc)) from exc
    # The primary email, else the first, else a userName that is one.
    emails = sorted(payload.emails, key=lambda email: not email.primary)
    email = emails[0].value if emails else payload.userName
    name = payload.name or _Name()
    return ScimUserAttributes(
        user_name=payload.userName.strip(),
        email=_normalize_email(email),
        active=payload.active,
        external_id=payload.externalId,
        given_name=name.givenName,
        family_name=name.familyName,
        display_name=payload.displayName,
    )


def _first_problem(exc: ValidationError) -> str:
    problem = exc.errors()[0]
    field = '.'.join(str(part) for part in problem['loc'])
    return f'{field}: {problem["msg"]}'


async def _patched_active(request: Request) -> bool:
    """The ``active`` value a PatchOp sets. Nothing else can be patched."""
    try:
        payload = _PatchPayload.model_validate(await _json_object(request))
    except ValidationError as exc:
        raise _invalid(_first_problem(exc)) from exc
    active: Any = None
    for operation in payload.Operations:
        if operation.op.lower() != 'replace':
            raise _invalid('Only the replace operation is supported', 'invalidPath')
        if operation.path is None and isinstance(operation.value, dict):
            if set(operation.value) != {'active'}:
                raise _invalid('Only active can be patched', 'invalidPath')
            active = operation.value['active']
        elif operation.path is not None and operation.path.lower() == 'active':
            active = operation.value
        else:
            raise _invalid('Only active can be patched', 'invalidPath')
        if not isinstance(active, bool):
            raise _invalid('active must be a JSON boolean')
    return active


def _parse_id(user_id: str) -> UUID:
    try:
        return UUID(user_id)
    except ValueError as exc:
        raise ScimError(404, 'User not found') from exc


def _resource(record: ScimUser, request: Request) -> dict[str, Any]:
    resource: dict[str, Any] = {
        'schemas': [USER_SCHEMA],
        'id': str(record.id),
        'userName': record.user_name,
        'name': {
            'givenName': record.given_name,
            'familyName': record.family_name,
        },
        'emails': [{'value': record.email, 'primary': True, 'type': 'work'}],
        'active': record.active,
        'meta': {
            'resourceType': 'User',
            'created': record.created_at.isoformat(),
            'lastModified': record.updated_at.isoformat(),
            'location': str(request.url_for('scim_get_user', user_id=str(record.id))),
        },
    }
    if record.external_id is not None:
        resource['externalId'] = record.external_id
    if record.display_name is not None:
        resource['displayName'] = record.display_name
    return resource


def _list_response(resources: list[dict[str, Any]], total: int, start: int) -> dict:
    return {
        'schemas': [LIST_SCHEMA],
        'totalResults': total,
        'startIndex': start,
        'itemsPerPage': len(resources),
        'Resources': resources,
    }


_FILTER = re.compile(
    r'^\s*(userName|externalId)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$', re.IGNORECASE
)


def _parse_filter(value: str | None) -> dict[str, str]:
    if value is None:
        return {}
    match = _FILTER.match(value)
    if match is None:
        raise _invalid(
            'Only userName eq "..." and externalId eq "..." are supported',
            'invalidFilter',
        )
    operand = re.sub(r'\\(.)', r'\1', match.group(2))
    key = 'user_name' if match.group(1).lower() == 'username' else 'external_id'
    return {key: operand}


def _parse_int(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise _invalid(f'{name} must be an integer') from exc


@scim_router.get('/Users')
async def scim_list_users(
    request: Request, service: ScimUserService = SCIM_SERVICE
) -> ScimResponse:
    start = max(_parse_int(request, 'startIndex', 1), 1)
    count = min(max(_parse_int(request, 'count', DEFAULT_PAGE_SIZE), 0), MAX_PAGE_SIZE)
    total, records = await ScimStore.list_users(
        service.org_id,
        offset=start - 1,
        limit=count,
        **_parse_filter(request.query_params.get('filter')),
    )
    resources = [_resource(record, request) for record in records]
    return ScimResponse(_list_response(resources, total, start))


@scim_router.post('/Users')
async def scim_create_user(
    request: Request, service: ScimUserService = SCIM_SERVICE
) -> ScimResponse:
    record = await service.create(await _user_attributes(request))
    resource = _resource(record, request)
    return ScimResponse(
        resource, status_code=201, headers={'Location': resource['meta']['location']}
    )


@scim_router.get('/Users/{user_id}')
async def scim_get_user(
    user_id: str, request: Request, service: ScimUserService = SCIM_SERVICE
) -> ScimResponse:
    record = await ScimStore.get_user(service.org_id, _parse_id(user_id))
    if record is None:
        raise ScimError(404, 'User not found')
    return ScimResponse(_resource(record, request))


@scim_router.put('/Users/{user_id}')
async def scim_replace_user(
    user_id: str, request: Request, service: ScimUserService = SCIM_SERVICE
) -> ScimResponse:
    record = await service.replace(_parse_id(user_id), await _user_attributes(request))
    return ScimResponse(_resource(record, request))


@scim_router.patch('/Users/{user_id}')
async def scim_patch_user(
    user_id: str, request: Request, service: ScimUserService = SCIM_SERVICE
) -> ScimResponse:
    record = await service.set_active(
        _parse_id(user_id), await _patched_active(request)
    )
    return ScimResponse(_resource(record, request))


_SERVICE_PROVIDER_CONFIG = {
    'schemas': ['urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig'],
    'patch': {'supported': True},
    'bulk': {'supported': False, 'maxOperations': 0, 'maxPayloadSize': 0},
    'filter': {'supported': True, 'maxResults': MAX_PAGE_SIZE},
    'changePassword': {'supported': False},
    'sort': {'supported': False},
    'etag': {'supported': False},
    'authenticationSchemes': [
        {
            'type': 'oauthbearertoken',
            'name': 'OAuth Bearer Token',
            'description': 'A SCIM token bound to one organization',
            'primary': True,
        }
    ],
    'meta': {'resourceType': 'ServiceProviderConfig'},
}

_USER_RESOURCE_TYPE = {
    'schemas': ['urn:ietf:params:scim:schemas:core:2.0:ResourceType'],
    'id': 'User',
    'name': 'User',
    'endpoint': '/Users',
    'schema': USER_SCHEMA,
    'meta': {'resourceType': 'ResourceType'},
}


def _attribute(name: str, kind: str = 'string', **extra: Any) -> dict[str, Any]:
    return {
        'name': name,
        'type': kind,
        'multiValued': False,
        'required': False,
        'mutability': 'readWrite',
        'returned': 'default',
        **extra,
    }


_USER_SCHEMA = {
    'schemas': ['urn:ietf:params:scim:schemas:core:2.0:Schema'],
    'id': USER_SCHEMA,
    'name': 'User',
    'attributes': [
        _attribute('userName', required=True, uniqueness='server'),
        _attribute('externalId'),
        _attribute('displayName'),
        _attribute(
            'name',
            'complex',
            subAttributes=[_attribute('givenName'), _attribute('familyName')],
        ),
        _attribute(
            'emails',
            'complex',
            multiValued=True,
            subAttributes=[_attribute('value'), _attribute('primary', 'boolean')],
        ),
        _attribute('active', 'boolean'),
    ],
    'meta': {'resourceType': 'Schema'},
}


@scim_router.get('/ServiceProviderConfig')
async def scim_service_provider_config(
    service: ScimUserService = SCIM_SERVICE,
) -> ScimResponse:
    return ScimResponse(_SERVICE_PROVIDER_CONFIG)


@scim_router.get('/ResourceTypes')
async def scim_resource_types(service: ScimUserService = SCIM_SERVICE) -> ScimResponse:
    return ScimResponse(_list_response([_USER_RESOURCE_TYPE], 1, 1))


@scim_router.get('/Schemas')
async def scim_schemas(service: ScimUserService = SCIM_SERVICE) -> ScimResponse:
    return ScimResponse(_list_response([_USER_SCHEMA], 1, 1))
