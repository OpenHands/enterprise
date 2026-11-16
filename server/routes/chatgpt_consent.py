"""Browser consent for a pre-registered ChatGPT OAuth client."""

import html
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from openhands.app_server.user_auth.user_auth import AuthType, get_user_auth
from server.auth.auth_error import NoCredentialsError
from server.auth.chatgpt_oauth import CODE_TTL, CloudMcpOAuth
from server.auth.saas_user_auth import SaasUserAuth


def create_consent_router(provider: CloudMcpOAuth) -> APIRouter:
    router = APIRouter()

    async def principal(request: Request) -> SaasUserAuth:
        auth = await get_user_auth(request)
        if not isinstance(auth, SaasUserAuth) or auth.auth_type != AuthType.COOKIE:
            raise HTTPException(403, 'Cloud sign-in is required')
        return auth

    @router.get('/chatgpt/consent')
    async def consent_page(
        request: Request, handle: str = Query(alias='request')
    ) -> Response:
        pending = await provider.consent_request(handle)
        if pending is None:
            raise HTTPException(400, 'Authorization request expired')
        try:
            user = await principal(request)
        except NoCredentialsError:
            return RedirectResponse(
                '/login?'
                + urlencode(
                    {'returnTo': f'/chatgpt/consent?{urlencode({"request": handle})}'}
                )
            )
        user_id = await user.get_user_id()
        if not user_id:
            raise HTTPException(403, 'Cloud sign-in is required')
        csrf = secrets.token_urlsafe(32)
        await provider.redis.set(
            f'chatgpt-oauth:csrf:{handle}:{user_id}', csrf, ex=CODE_TTL
        )
        form = (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>Connect ChatGPT to OpenHands</title>'
            '<h1>Connect ChatGPT to OpenHands?</h1>'
            '<p>Allow ChatGPT to read your OpenHands tasks in your current organization. '
            'You can disconnect it later.</p>'
            '<form method="post" action="/chatgpt/consent">'
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
                'Content-Security-Policy': "default-src 'none'; form-action 'self'",
            },
        )

    @router.post('/chatgpt/consent')
    async def submit_consent(
        request: Request,
        handle: str = Form(...),
        csrf: str = Form(...),
        decision: str = Form(...),
    ) -> RedirectResponse:
        user = await principal(request)
        user_id = await user.get_user_id()
        if not user_id:
            raise HTTPException(403, 'Cloud sign-in is required')
        expected = await provider.redis.getdel(f'chatgpt-oauth:csrf:{handle}:{user_id}')
        if expected is None or not secrets.compare_digest(expected, csrf.encode()):
            raise HTTPException(403, 'Invalid consent request')
        if decision == 'deny':
            destination = await provider.deny(handle)
        elif decision == 'allow':
            org_id = await user.get_effective_org_id()
            if org_id is None:
                raise HTTPException(403, 'Organization access is required')
            destination = await provider.approve(handle, user_id, str(org_id))
        else:
            raise HTTPException(400, 'Invalid decision')
        if destination is None:
            raise HTTPException(400, 'Authorization request expired')
        return RedirectResponse(destination, status_code=303)

    return router
