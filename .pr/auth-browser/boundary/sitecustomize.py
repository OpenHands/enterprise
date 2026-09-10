"""QA-only remote transport rewrite; account/auth/database services remain real."""

import os

import httpx

_original_send = httpx.AsyncClient.send


async def _external_send(self, request, *args, **kwargs):
    if request.url.host == 'api.github.com':
        request.url = request.url.copy_with(
            scheme='http',
            host='127.0.0.1',
            port=int(os.environ['AUTH_QA_EXTERNAL_PORT']),
        )
    return await _original_send(self, request, *args, **kwargs)


httpx.AsyncClient.send = _external_send
