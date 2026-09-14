"""Native authentication APIs. Registration is restricted to native installs."""

from collections.abc import Callable, Coroutine
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, SecretStr

from server.auth import auth_config
from server.auth.native_password import NativeAuthError
from server.auth.native_session import (
    SESSION_COOKIE,
    clear_session_cookie,
    set_anonymous_csrf_cookie,
    set_session_cookie,
)
from server.services.native_auth_service import get_native_auth_service

NO_STORE = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}


class NativeAuthRoute(APIRoute):
    """Redact validation inputs and prevent caching of credential responses."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            if auth_config.ENABLE_KEYCLOAK:
                raise HTTPException(404, 'Not found', headers=NO_STORE)
            try:
                response = await original(request)
            except NativeAuthError as exc:
                raise HTTPException(
                    exc.status_code, str(exc), headers=NO_STORE
                ) from exc
            except RequestValidationError as exc:
                # FastAPI's default validation response includes raw body inputs.
                raise HTTPException(
                    422, 'Invalid authentication request', headers=NO_STORE
                ) from exc
            response.headers.update(NO_STORE)
            return response

        return handler


native_auth_router = APIRouter(
    prefix='/api/auth', tags=['Authentication'], route_class=NativeAuthRoute
)


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: SecretStr
    return_path: str | None = Field(default=None, max_length=2048)


def client_ip(request: Request) -> str:
    # Proxy forwarding headers are not authentication authority. Deployments may
    # configure the ASGI server's trusted-proxy handling of request.client.
    return request.client.host if request.client else 'unknown'


def authenticated_id(user_id: str | None) -> UUID:
    if user_id is None:
        raise HTTPException(401, 'Not authenticated', headers=NO_STORE)
    try:
        return UUID(user_id)
    except ValueError as exc:
        raise HTTPException(401, 'Not authenticated', headers=NO_STORE) from exc


@native_auth_router.get('/csrf')
async def csrf(request: Request, response: Response) -> dict[str, str]:
    service = get_native_auth_service()
    await service.throttle('csrf', client_ip(request))
    token, anonymous = await service.issue_csrf(request.cookies.get(SESSION_COOKIE))
    if anonymous is not None:
        clear_session_cookie(response, request)
        set_anonymous_csrf_cookie(response, anonymous, request)
    return {'csrf_token': token}


@native_auth_router.post('/password/login')
async def login(
    body: LoginBody, request: Request, response: Response
) -> dict[str, str]:
    service = get_native_auth_service()
    result = await service.login(
        body.email,
        body.password.get_secret_value(),
        client_ip=client_ip(request),
        return_path=body.return_path,
    )
    if old := request.cookies.get(SESSION_COOKIE):
        await service.revoke_session(old)
    set_session_cookie(response, result.token, request)
    return {'redirect_to': result.redirect_to}
