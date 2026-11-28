"""Browser consent for a registered external MCP client."""

import secrets
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from openhands.app_server.user_auth.user_auth import AuthType, get_user_auth
from server.auth.auth_error import NoCredentialsError
from server.auth.external_mcp_oauth import (
    CODE_TTL,
    WRITE_SCOPE,
    ExternalMcpOAuth,
    granted_scopes,
)
from server.auth.saas_user_auth import SaasUserAuth
from server.constants import REQUIRE_VERIFIED_EMAILS
from server.routes.external_mcp_consent_page import (
    CONSENT_STYLE_HASH,
    render_callback_handoff,
    render_consent_page,
)

CONSENT_PATH = '/external-mcp/consent'


def create_consent_router(
    provider: ExternalMcpOAuth, *, consent_path: str = CONSENT_PATH
) -> APIRouter:
    """Browser consent for ``provider``'s endpoint.

    ``consent_path`` defaults to the legacy external-mcp path; a second
    endpoint on a different mount passes its own path here. The CSRF key
    namespace and the "is this endpoint on for this org" check both come from
    ``provider`` (``provider.redis_key_prefix`` and ``provider.feature_enabled``),
    so they automatically follow that endpoint's own configuration rather than
    the legacy endpoint's.
    """
    router = APIRouter()

    async def principal(request: Request) -> tuple[str, str]:
        """The signed-in user and org, when the feature is on for them."""
        auth = await get_user_auth(request)
        if not isinstance(auth, SaasUserAuth) or auth.auth_type != AuthType.COOKIE:
            raise HTTPException(403, 'OpenHands sign-in is required')
        user_id = await auth.get_user_id()
        if not user_id:
            raise HTTPException(403, 'OpenHands sign-in is required')
        # /external-mcp is outside SetAuthCookieMiddleware's API guards.
        # Apply the same cookie terms and configured email-verification rules
        # before rendering consent or approving a grant.
        if auth.accepted_tos is False:
            raise HTTPException(403, 'Terms of service acceptance is required')
        if REQUIRE_VERIFIED_EMAILS and not auth.email_verified:
            raise HTTPException(403, 'Email verification is required')
        org_id = await auth.get_effective_org_id()
        if org_id is None:
            raise HTTPException(403, 'Organization access is required')
        if not await provider.feature_enabled(user_id, str(org_id)):
            raise HTTPException(
                403, 'Connecting external tools is not enabled for this organization'
            )
        return user_id, str(org_id)

    @router.get(consent_path)
    async def consent_page(
        request: Request, handle: str = Query(alias='request')
    ) -> Response:
        pending = await provider.consent_request(handle)
        if pending is None:
            raise HTTPException(400, 'Authorization request expired')
        try:
            user_id, _ = await principal(request)
        except NoCredentialsError:
            return RedirectResponse(
                '/login?'
                + urlencode(
                    {'returnTo': f'{consent_path}?{urlencode({"request": handle})}'}
                )
            )
        csrf = secrets.token_urlsafe(32)
        await provider.redis.set(
            f'{provider.redis_key_prefix}:csrf:{handle}:{user_id}', csrf, ex=CODE_TTL
        )
        redirect = urlsplit(pending['redirect_uri'])
        form = render_consent_page(
            provider.clients[pending['client_id']].name,
            redirect.netloc,
            handle,
            csrf,
            can_write=WRITE_SCOPE in granted_scopes(pending),
            consent_path=consent_path,
        )
        return HTMLResponse(
            form,
            headers={
                'Cache-Control': 'no-store',
                'Referrer-Policy': 'no-referrer',
                # The POST terminates on an OpenHands handoff document. The
                # client callback is then a separate navigation, not a redirect
                # chain governed by this form's CSP.
                'Content-Security-Policy': (
                    f"default-src 'none'; style-src 'sha256-{CONSENT_STYLE_HASH}'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                ),
            },
        )

    @router.post(consent_path)
    async def submit_consent(
        request: Request,
        handle: str = Form(...),
        csrf: str = Form(...),
        decision: str = Form(...),
    ) -> HTMLResponse:
        user_id, org_id = await principal(request)
        expected = await provider.redis.getdel(
            f'{provider.redis_key_prefix}:csrf:{handle}:{user_id}'
        )
        if expected is None or not secrets.compare_digest(expected, csrf.encode()):
            raise HTTPException(403, 'Invalid consent request')
        if decision == 'deny':
            destination = await provider.deny(handle)
        elif decision == 'allow':
            destination = await provider.approve(handle, user_id, org_id)
        else:
            raise HTTPException(400, 'Invalid decision')
        if destination is None:
            raise HTTPException(400, 'Authorization request expired')
        return HTMLResponse(
            render_callback_handoff(destination),
            headers={
                'Cache-Control': 'no-store',
                'Referrer-Policy': 'no-referrer',
                'Content-Security-Policy': (
                    f"default-src 'none'; style-src 'sha256-{CONSENT_STYLE_HASH}'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
                ),
            },
        )

    return router
