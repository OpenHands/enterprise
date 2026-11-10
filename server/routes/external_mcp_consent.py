"""Browser consent for a registered external MCP client."""

import html
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
    feature_enabled,
    granted_scopes,
)
from server.auth.saas_user_auth import SaasUserAuth

CONSENT_PATH = '/external-mcp/consent'


def create_consent_router(provider: ExternalMcpOAuth) -> APIRouter:
    router = APIRouter()

    async def principal(request: Request) -> tuple[str, str]:
        """The signed-in user and org, when the feature is on for them."""
        auth = await get_user_auth(request)
        if not isinstance(auth, SaasUserAuth) or auth.auth_type != AuthType.COOKIE:
            raise HTTPException(403, 'OpenHands sign-in is required')
        user_id = await auth.get_user_id()
        if not user_id:
            raise HTTPException(403, 'OpenHands sign-in is required')
        org_id = await auth.get_effective_org_id()
        if org_id is None:
            raise HTTPException(403, 'Organization access is required')
        if not await feature_enabled(user_id, str(org_id)):
            raise HTTPException(
                403, 'Connecting external tools is not enabled for this organization'
            )
        return user_id, str(org_id)

    @router.get(CONSENT_PATH)
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
                    {'returnTo': f'{CONSENT_PATH}?{urlencode({"request": handle})}'}
                )
            )
        csrf = secrets.token_urlsafe(32)
        await provider.redis.set(
            f'external-mcp-oauth:csrf:{handle}:{user_id}', csrf, ex=CODE_TTL
        )
        client_name = html.escape(provider.clients[pending['client_id']].name)
        redirect = urlsplit(pending['redirect_uri'])
        return_host = html.escape(redirect.netloc)
        form = (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            f'<title>Connect {client_name} to OpenHands</title>'
            f'<h1>Connect {client_name} to OpenHands?</h1>'
            f'<p>Allow {client_name} to read your OpenHands tasks in your current '
            'organization. You can disconnect it later.</p>'
            + (
                f'<p><strong>{client_name} will also be able to start new tasks '
                'and send follow-up messages. Those tasks run agents with your '
                'access, including your connected Git accounts and secrets.'
                '</strong></p>'
                if WRITE_SCOPE in granted_scopes(pending)
                else ''
            )
            + f'<p>After you choose, you will be sent back to {return_host}.</p>'
            f'<form method="post" action="{CONSENT_PATH}">'
            f'<input type="hidden" name="handle" value="{html.escape(handle, quote=True)}">'
            f'<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">'
            '<button name="decision" value="allow" type="submit">Allow</button>'
            '<button name="decision" value="deny" type="submit">Deny</button>'
            '</form></html>'
        )
        return HTMLResponse(
            form,
            headers={
                'Cache-Control': 'no-store',
                'Referrer-Policy': 'no-referrer',
                # Chrome applies form-action to the redirect that follows the
                # POST, so the registered client's origin must be allowed too.
                'Content-Security-Policy': (
                    "default-src 'none'; form-action 'self' "
                    f'{redirect.scheme}://{redirect.netloc}'
                ),
            },
        )

    @router.post(CONSENT_PATH)
    async def submit_consent(
        request: Request,
        handle: str = Form(...),
        csrf: str = Form(...),
        decision: str = Form(...),
    ) -> RedirectResponse:
        user_id, org_id = await principal(request)
        expected = await provider.redis.getdel(
            f'external-mcp-oauth:csrf:{handle}:{user_id}'
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
        return RedirectResponse(destination, status_code=303)

    return router
