"""OpenHands-owned local authentication HTTP contracts."""

from collections.abc import Callable
from functools import wraps
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from server.auth.browser_security import (
    SESSION_COOKIE,
    auth_email_configured,
    auth_redirect,
    clear_session_cookie,
    csrf_seed,
    safe_redirect,
    set_session_cookie,
    validate_csrf,
    web_origin,
)
from server.auth.contracts import InvalidCredentials, IssuedSession, Principal
from server.auth.http import AuthValidationRoute
from server.auth.local.accounts import AccountConflict
from server.auth.local.actions import (
    ActionEmail,
    InvalidActionToken,
    LocalAccountActions,
)
from server.auth.local.credentials import LocalPasswordCredentialService
from server.auth.local.passwords import PasswordPolicyError
from server.auth.local.sessions import LocalBrowserSessionBackend
from server.auth.local.throttle import throttle
from server.auth.mode import AuthMode, get_auth_mode
from server.services.smtp_email_service import SMTPEmailService


class AuthRoute(AuthValidationRoute):
    def get_route_handler(self) -> Callable:
        from server.auth.user_management import AccountConflict as ManagementConflict
        from server.auth.user_management import AccountPermissionError

        handler = super().get_route_handler()

        @wraps(handler)
        async def sanitized(request: Request):
            try:
                return await handler(request)
            except InvalidCredentials:
                raise HTTPException(
                    401, 'Invalid credentials or expired session.'
                ) from None
            except AccountPermissionError:
                raise HTTPException(403, 'Permission denied.') from None
            except (AccountConflict, ManagementConflict, IntegrityError):
                raise HTTPException(
                    409, 'An account already uses this email address.'
                ) from None
            except SQLAlchemyError:
                raise HTTPException(
                    503, 'Authentication is temporarily unavailable.'
                ) from None
            except InvalidActionToken:
                raise HTTPException(400, detail={'code': 'invalid_token'}) from None
            except PasswordPolicyError:
                raise HTTPException(400, detail={'code': 'invalid_password'}) from None
            except ValueError:
                raise HTTPException(400, 'Invalid authentication request.') from None

        return sanitized


router = APIRouter(prefix='/api/auth', tags=['Authentication'], route_class=AuthRoute)


class AuthInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    redirect_url: str = '/'
    invitation_token: SecretStr | None = None


class LoginInput(AuthInput):
    email: str
    password: SecretStr
    invitation_token: SecretStr | None = None


class ChangePasswordInput(AuthInput):
    current_password: SecretStr
    new_password: SecretStr
    invitation_token: SecretStr | None = None


class ForgotPasswordInput(AuthInput):
    email: str


class ResetPasswordInput(AuthInput):
    token: SecretStr
    new_password: SecretStr


class VerificationInput(AuthInput):
    invitation_token: SecretStr | None = None


class ChangeEmailInput(VerificationInput):
    email: str


class VerifyEmailInput(VerificationInput):
    token: SecretStr


class EnrollInput(AuthInput):
    invitation_token: SecretStr
    password: SecretStr


class CreateAccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    email: str
    initial_password: SecretStr
    first_name: str | None = None
    last_name: str | None = None


class LoginResult(BaseModel):
    password_change_required: bool
    redirect_url: str


class RedirectResult(BaseModel):
    redirect_url: str


class MessageResult(BaseModel):
    message: str


class AccountResult(BaseModel):
    id: str
    email: str
    password_change_required: Literal[True] = True


def require_local(request: Request) -> None:
    if get_auth_mode() is not AuthMode.LOCAL:
        raise HTTPException(404, 'Local authentication is unavailable.')
    validate_csrf(request)


def session_backend() -> LocalBrowserSessionBackend:
    return LocalBrowserSessionBackend()


def password_service() -> LocalPasswordCredentialService:
    return LocalPasswordCredentialService()


def account_actions() -> LocalAccountActions:
    return LocalAccountActions()


async def local_principal(
    request: Request, backend: LocalBrowserSessionBackend = Depends(session_backend)
) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, 'Authentication required.')
    return await backend.validate(SecretStr(token))


async def unrestricted_principal(
    principal: Principal = Depends(local_principal),
) -> Principal:
    if principal.restricted:
        raise HTTPException(
            403,
            detail={
                'code': 'password_change_required',
                'redirect_url': '/auth/change-password',
            },
        )
    return principal


def _invitation(token: SecretStr | None) -> str | None:
    return token.get_secret_value() if token else None


async def _login_response(
    response: Response,
    issued: IssuedSession,
    redirect_url: str,
    invitation_token: SecretStr | None,
    backend: LocalBrowserSessionBackend,
) -> dict[str, Any]:
    destination = safe_redirect(redirect_url)
    if issued.principal.restricted:
        destination = auth_redirect(
            '/auth/change-password', destination, _invitation(invitation_token)
        )
    else:
        from server.auth.admission import complete_local_login

        try:
            destination = await complete_local_login(
                issued.principal.user_id, destination, _invitation(invitation_token)
            )
        except Exception:
            await backend.revoke(issued.token)
            raise
    set_session_cookie(response, issued.token.get_secret_value())
    return {
        'password_change_required': issued.principal.restricted,
        'redirect_url': safe_redirect(destination),
    }


def _deliver(
    email: ActionEmail | None,
    redirect_url: str,
    invitation_token: SecretStr | None = None,
) -> None:
    if email is None:
        return
    path = (
        '/auth/reset-password'
        if email.purpose == 'password_reset'
        else '/auth/verify-email'
    )
    params = {
        'returnTo': safe_redirect(redirect_url),
    }
    if invitation_token:
        params['invitation_token'] = invitation_token.get_secret_value()
    # URL origin is operator-configured, never supplied by the requester.
    SMTPEmailService.send_auth_email(
        email.email,
        email.purpose,
        f'{web_origin()}{path}?{urlencode(params)}#'
        + urlencode({'token': email.token.get_secret_value()}),
    )


GENERIC_MESSAGE = {
    'message': 'If this request can be completed, an email will arrive shortly.'
}


@router.get('/csrf')
async def get_csrf(request: Request, response: Response) -> dict[str, str]:
    return {'csrf_token': csrf_seed(request, response)}


@router.post(
    '/login', dependencies=[Depends(require_local)], response_model=LoginResult
)
async def login(
    data: LoginInput,
    request: Request,
    response: Response,
    passwords: LocalPasswordCredentialService = Depends(password_service),
    backend: LocalBrowserSessionBackend = Depends(session_backend),
):
    safe_redirect(data.redirect_url)
    await throttle(request, data.email)
    issued = await passwords.login(data.email, data.password)
    return await _login_response(
        response, issued, data.redirect_url, data.invitation_token, backend
    )


@router.post(
    '/password/change',
    dependencies=[Depends(require_local)],
    response_model=LoginResult,
)
async def change_password(
    data: ChangePasswordInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(local_principal),
    passwords: LocalPasswordCredentialService = Depends(password_service),
    backend: LocalBrowserSessionBackend = Depends(session_backend),
):
    safe_redirect(data.redirect_url)
    await throttle(request, str(principal.user_id))
    issued = await passwords.change_and_issue(
        principal.user_id, data.current_password, data.new_password
    )
    return await _login_response(
        response, issued, data.redirect_url, data.invitation_token, backend
    )


@router.post(
    '/password/forgot',
    dependencies=[Depends(require_local)],
    response_model=MessageResult,
)
async def forgot_password(
    data: ForgotPasswordInput,
    request: Request,
    tasks: BackgroundTasks,
    actions: LocalAccountActions = Depends(account_actions),
):
    safe_redirect(data.redirect_url)
    await throttle(request, data.email, recovery=True)
    email = await actions.request_reset(data.email) if auth_email_configured() else None
    tasks.add_task(_deliver, email, data.redirect_url, data.invitation_token)
    return GENERIC_MESSAGE


@router.post(
    '/password/reset',
    dependencies=[Depends(require_local)],
    response_model=RedirectResult,
)
async def reset_password(
    data: ResetPasswordInput,
    request: Request,
    response: Response,
    actions: LocalAccountActions = Depends(account_actions),
):
    safe_redirect(data.redirect_url)
    await throttle(request, data.token.get_secret_value(), recovery=True)
    await actions.reset_password(data.token, data.new_password)
    clear_session_cookie(response)
    return {
        'redirect_url': auth_redirect(
            '/login', data.redirect_url, _invitation(data.invitation_token)
        )
    }


@router.post(
    '/email/request-verification',
    dependencies=[Depends(require_local)],
    response_model=MessageResult,
)
async def request_verification(
    data: VerificationInput,
    request: Request,
    tasks: BackgroundTasks,
    principal: Principal = Depends(unrestricted_principal),
    actions: LocalAccountActions = Depends(account_actions),
):
    safe_redirect(data.redirect_url)
    await throttle(request, str(principal.user_id), recovery=True)
    email = (
        await actions.request_verification(principal.user_id)
        if auth_email_configured()
        else None
    )
    tasks.add_task(_deliver, email, data.redirect_url, data.invitation_token)
    return GENERIC_MESSAGE


@router.post(
    '/email/change', dependencies=[Depends(require_local)], response_model=MessageResult
)
async def change_email(
    data: ChangeEmailInput,
    request: Request,
    tasks: BackgroundTasks,
    principal: Principal = Depends(unrestricted_principal),
    actions: LocalAccountActions = Depends(account_actions),
):
    safe_redirect(data.redirect_url)
    await throttle(request, str(principal.user_id), recovery=True)
    from server.auth.user_management import EnterpriseUserManagementService

    email = (
        await EnterpriseUserManagementService().request_email_change(
            principal.user_id, data.email, actor=principal
        )
        if auth_email_configured()
        else None
    )
    tasks.add_task(_deliver, email, data.redirect_url, data.invitation_token)
    return GENERIC_MESSAGE


@router.post(
    '/email/verify',
    dependencies=[Depends(require_local)],
    response_model=RedirectResult,
)
async def verify_email(
    data: VerifyEmailInput,
    request: Request,
    actions: LocalAccountActions = Depends(account_actions),
    backend: LocalBrowserSessionBackend = Depends(session_backend),
):
    safe_redirect(data.redirect_url)
    await throttle(request, data.token.get_secret_value(), recovery=True)
    user_id = await actions.verify_email(data.token)
    token = request.cookies.get(SESSION_COOKIE)
    principal = None
    if token:
        try:
            principal = await backend.validate(SecretStr(token))
        except InvalidCredentials:
            pass
    if principal and principal.user_id == user_id and not principal.restricted:
        from server.auth.admission import complete_local_login

        destination = await complete_local_login(
            user_id, data.redirect_url, _invitation(data.invitation_token)
        )
    else:
        destination = auth_redirect(
            '/login', data.redirect_url, _invitation(data.invitation_token)
        )
    return {'redirect_url': safe_redirect(destination)}


@router.post(
    '/invitations/enroll',
    dependencies=[Depends(require_local)],
    response_model=LoginResult,
)
async def enroll(
    data: EnrollInput,
    request: Request,
    response: Response,
    actions: LocalAccountActions = Depends(account_actions),
    backend: LocalBrowserSessionBackend = Depends(session_backend),
):
    safe_redirect(data.redirect_url)
    await throttle(request, data.invitation_token.get_secret_value(), recovery=True)
    issued = await actions.enroll(data.invitation_token, data.password)
    # The invitation has already been consumed atomically with membership.
    return await _login_response(response, issued, data.redirect_url, None, backend)


@router.post(
    '/accounts', dependencies=[Depends(require_local)], response_model=AccountResult
)
async def create_account(
    data: CreateAccountInput, principal: Principal = Depends(unrestricted_principal)
):
    from server.auth.user_management import EnterpriseUserManagementService

    user = await EnterpriseUserManagementService().create(
        data.email,
        data.initial_password,
        actor=principal,
        first_name=data.first_name,
        last_name=data.last_name,
    )
    return {
        'id': str(user.id),
        'email': user.email,
        'password_change_required': True,
    }
