"""Routes mounted only when CUSTOMER_GATEWAY_ENABLED=true (see saas_server.py).

- /api/customer-gateway/v1/{chat/completions,models}: relay; accepts only inference tokens.
- /api/customer-gateway/token: sandbox-authenticated (X-Session-API-Key) re-mint for the
  agent-server refresh-on-401 hook. Returns an inference token, never a customer key.
There is deliberately no HTTP route that writes or reads customer keys (operator CLI only)."""

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from openhands.app_server.sandbox.session_auth import validate_session_key
from server.customer_gateway.relay import InferenceRelay, RelayConfig
from server.customer_gateway.service import (
    authorize_inference,
    mint_for_sandbox,
    org_gateway_for_sandbox,
)
from storage.customer_gateway_store import CustomerGatewayDenied

# Per-sandbox bearer selected by a custom header: no shared cache may store or reuse it.
NO_STORE = {
    'Cache-Control': 'no-store, private',
    'Pragma': 'no-cache',
    'Vary': 'X-Session-API-Key',
}


def build_router() -> APIRouter:
    router = APIRouter(prefix='/api/customer-gateway', include_in_schema=False)
    relay = InferenceRelay(RelayConfig(), authorize_inference)
    for route in relay.routes():
        # Starlette add_route ignores APIRouter.prefix, so the full path is spelled out.
        router.add_route(
            router.prefix + route.path,
            route.endpoint,
            methods=list(route.methods or []),
        )

    @router.get('/token', response_class=PlainTextResponse)
    async def current_inference_token(request: Request) -> PlainTextResponse:
        try:
            sandbox = await validate_session_key(
                request.headers.get('X-Session-API-Key')
            )
            gw = await org_gateway_for_sandbox(sandbox)
            if gw is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail='No customer gateway for this sandbox',
                )
            try:
                token = await mint_for_sandbox(sandbox, gw)
            except CustomerGatewayDenied as exc:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, detail=exc.reason
                ) from exc
        except HTTPException as exc:
            raise HTTPException(
                exc.status_code, detail=exc.detail, headers=NO_STORE
            ) from exc
        return PlainTextResponse(token, headers=NO_STORE)

    return router
