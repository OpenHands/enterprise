from __future__ import annotations

import hashlib
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field, SecretStr

from openhands.app_server.user_auth import get_user_id
from server.auth.oauth_v2_refresh import sign_oauth_v2_cookie
from server.auth.password_auth import (
    PASSWORD_MIN_LENGTH,
    PASSWORD_SESSION_MAX_AGE_SECONDS,
    PasswordAuthError,
    is_password_auth_enabled,
)
from server.services.password_auth_service import PasswordAuthService
from server.utils.rate_limit_utils import check_rate_limit_by_user_id
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url

password_auth_router = APIRouter(prefix='/api/auth/password', tags=['Authentication'])
password_admin_router = APIRouter(
    prefix='/api/organizations/{org_id}/members', tags=['Organization Members']
)


class PasswordLoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr


class PasswordTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class CompletePasswordRequest(PasswordTokenRequest):
    password: SecretStr


class PasswordTokenResponse(BaseModel):
    status: str
    email: str = ''
    purpose: str = ''
    expires_at: datetime | None = None
    minimum_password_length: int = PASSWORD_MIN_LENGTH


class PasswordLinkResponse(BaseModel):
    url: str
    expires_at: datetime
    purpose: str


def _raise_auth_error(exc: PasswordAuthError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={'message': str(exc), 'code': exc.code},
    ) from exc


def _set_password_cookie(
    request: Request,
    response: Response,
    *,
    user_id: UUID,
    session_version: int,
) -> None:
    payload = {
        'user_id': str(user_id),
        'access_token_expires_at': None,
        'accepted_tos': None,
        'auth_method': 'password',
        'session_version': session_version,
    }
    response.set_cookie(
        key='openhands_auth',
        value=sign_oauth_v2_cookie(payload, PASSWORD_SESSION_MAX_AGE_SECONDS),
        max_age=PASSWORD_SESSION_MAX_AGE_SECONDS,
        domain=get_cookie_domain(),
        secure=get_web_url(request).startswith('https'),
        httponly=True,
        samesite=get_cookie_samesite(),
    )


def build_password_link(request: Request, token: str) -> str:
    return f'{get_web_url(request)}/set-password#token={token}'


@password_auth_router.get('/status')
async def password_auth_status() -> dict[str, bool | int]:
    return {
        'enabled': is_password_auth_enabled(),
        'minimum_password_length': PASSWORD_MIN_LENGTH,
    }


@password_auth_router.post('/login')
async def password_login(
    body: PasswordLoginRequest, request: Request, response: Response
) -> dict[str, bool]:
    digest = hashlib.sha256(str(body.email).casefold().encode()).hexdigest()
    await check_rate_limit_by_user_id(
        request,
        'password_login',
        digest,
        user_rate_limit_seconds=2,
        ip_rate_limit_seconds=2,
    )
    try:
        user_id, version = await PasswordAuthService.login(
            str(body.email), body.password.get_secret_value()
        )
    except PasswordAuthError as exc:
        _raise_auth_error(exc)
    _set_password_cookie(request, response, user_id=user_id, session_version=version)
    return {'success': True}


@password_auth_router.post('/inspect', response_model=PasswordTokenResponse)
async def inspect_password_token(body: PasswordTokenRequest) -> PasswordTokenResponse:
    try:
        inspection = await PasswordAuthService.inspect_token(body.token)
    except PasswordAuthError as exc:
        _raise_auth_error(exc)
    return PasswordTokenResponse(
        status=inspection.status,
        email=inspection.email,
        purpose=inspection.purpose,
        expires_at=inspection.expires_at,
    )


@password_auth_router.post('/complete')
async def complete_password_token(
    body: CompletePasswordRequest, request: Request, response: Response
) -> dict[str, bool]:
    try:
        user_id, version = await PasswordAuthService.complete_token(
            body.token, body.password.get_secret_value()
        )
    except PasswordAuthError as exc:
        _raise_auth_error(exc)
    _set_password_cookie(request, response, user_id=user_id, session_version=version)
    return {'success': True}


@password_admin_router.post(
    '/{target_user_id}/password-reset', response_model=PasswordLinkResponse
)
async def issue_password_reset(
    org_id: UUID,
    target_user_id: UUID,
    request: Request,
    caller_user_id: str = Depends(get_user_id),
) -> PasswordLinkResponse:
    try:
        link = await PasswordAuthService.issue_reset_link(
            org_id, target_user_id, UUID(caller_user_id)
        )
    except PasswordAuthError as exc:
        _raise_auth_error(exc)
    return PasswordLinkResponse(
        url=build_password_link(request, link.token),
        expires_at=link.expires_at,
        purpose=link.purpose,
    )
