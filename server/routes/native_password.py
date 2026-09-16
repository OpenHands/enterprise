"""Password replacement APIs."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, SecretStr

from openhands.app_server.user_auth import get_user_id
from server.auth.native_session import SESSION_COOKIE
from server.routes.native_auth import NativeAuthRoute, authenticated_id, client_ip
from server.services.native_password_service import get_native_password_service

native_password_router = APIRouter(
    prefix='/api/auth', tags=['Authentication'], route_class=NativeAuthRoute
)


class ResetBody(BaseModel):
    token: SecretStr
    new_password: SecretStr


class ChangeBody(BaseModel):
    current_password: SecretStr
    new_password: SecretStr


@native_password_router.post('/password/change', status_code=204)
async def change_password(
    body: ChangeBody,
    request: Request,
    user_id: str | None = Depends(get_user_id),
) -> None:
    await get_native_password_service().change_password(
        authenticated_id(user_id),
        request.cookies.get(SESSION_COOKIE, ''),
        body.current_password.get_secret_value(),
        body.new_password.get_secret_value(),
        client_ip=client_ip(request),
    )


@native_password_router.post('/password/reset/complete', status_code=204)
async def complete_password_reset(body: ResetBody, request: Request) -> None:
    await get_native_password_service().complete_password_reset(
        body.token.get_secret_value(),
        body.new_password.get_secret_value(),
        client_ip=client_ip(request),
    )
