"""Invitation inspection and account enrollment APIs."""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, SecretStr

from openhands.app_server.user_auth import get_user_id
from server.auth.native_session import set_session_cookie
from server.auth.native_types import InvitationInspection
from server.routes.native_auth import NativeAuthRoute, authenticated_id, client_ip
from server.services.native_enrollment_service import get_native_enrollment_service

native_enrollment_router = APIRouter(
    prefix='/api/auth', tags=['Authentication'], route_class=NativeAuthRoute
)


class TokenBody(BaseModel):
    token: SecretStr


class EnrollmentBody(TokenBody):
    password: SecretStr


@native_enrollment_router.post('/enrollment/inspect')
async def inspect_enrollment(body: TokenBody, request: Request) -> InvitationInspection:
    service = get_native_enrollment_service()
    await service.auth.throttle('enrollment_inspect', client_ip(request))
    return await service.inspect_invitation(body.token.get_secret_value())


@native_enrollment_router.post('/enrollment/complete')
async def complete_enrollment(
    body: EnrollmentBody, request: Request, response: Response
) -> dict[str, str]:
    service = get_native_enrollment_service()
    result = await service.complete_enrollment(
        body.token.get_secret_value(),
        body.password.get_secret_value(),
        client_ip=client_ip(request),
    )
    if result is None:
        return {'action': 'login'}
    await set_session_cookie(response, result.token, request)
    return {'action': 'complete', 'redirect_to': result.redirect_to}


@native_enrollment_router.post('/enrollment/accept-membership', status_code=204)
async def accept_membership(
    body: TokenBody, user_id: str | None = Depends(get_user_id)
) -> None:
    await get_native_enrollment_service().accept_membership(
        authenticated_id(user_id), body.token.get_secret_value()
    )
