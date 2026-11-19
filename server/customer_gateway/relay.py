"""Fixed-destination inference relay (Starlette routes, HTTPX upstream).

Only POST /v1/chat/completions and GET /v1/models. Upstream URL and
Authorization are built server-side from admin config and the member mapping;
no client header is forwarded. No redirects, no retries, streaming passthrough.
Relay-side denials that confirm a sandbox/member is actually inactive are 4xx
(never 5xx) so the SDK does not retry them. The one exception is a transient
failure to confirm the sandbox's live status (e.g. a runtime-API error): that
is reported as a retryable 503, not folded into the non-retryable 403, since
it says nothing about whether the sandbox is actually inactive.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import anyio
import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from server.customer_gateway.profile import GatewayProfile
from server.customer_gateway.service import CustomerGatewaySandboxLookupUnavailable
from server.customer_gateway.tokens import InvalidInferenceToken, MissingInferenceToken
from storage.customer_gateway_store import CustomerGatewayDenied as Denied

logger = logging.getLogger('customer_gateway.relay')
CLOSE_TIMEOUT_S = 5
ERROR_BODY_LIMIT = 64 * 1024

ALLOWED_BODY_KEYS = frozenset(
    {
        'model',
        'messages',
        'stream',
        'stream_options',
        'temperature',
        'top_p',
        'max_tokens',
        'max_completion_tokens',
        'stop',
        'tools',
        'tool_choice',
        'prompt_cache_key',
        'parallel_tool_calls',
        'response_format',
        'seed',
        'presence_penalty',
        'frequency_penalty',
        'reasoning_effort',
        'logprobs',
        'top_logprobs',
        'n',
    }
)
PASSBACK_HEADERS = ('content-type', 'content-encoding', 'retry-after')


Authorizer = Callable[[Request], Awaitable[tuple[Any, GatewayProfile, Any]]]


@dataclass
class RelayConfig:
    max_body_bytes: int = 4 * 1024 * 1024
    connect_timeout: float = 10.0
    read_timeout: float = 600.0
    max_inflight: int = 64  # per-process cap on concurrent upstream requests
    max_inflight_per_user: int = (
        8  # per-process, per member: one member cannot hold all
    )


# A single short, bounded wait for capacity to free up before denying with 429.
# Not a queue: at most `max_inflight` requests wait at once (see `_waiting`),
# each for exactly one sleep, with no upstream call while waiting. Authorization
# is re-checked after the wait (see `_authorize_or_deny`'s `timeout`) so a
# request can't be forwarded on a sandbox/member/policy state that went stale
# while it waited.
CAPACITY_WAIT_S = 0.25

MISSING_TOKEN_MESSAGE = (
    'Customer gateway: no OpenHands inference token. LLMs must come from the '
    'conversation start request; standalone SDK use (e.g. CloudWorkspace.get_llm) '
    'is unsupported for this organization.'
)


def _deny(
    status: int, code: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {'error': {'type': 'relay_denied', 'code': code}},
        status_code=status,
        headers=headers,
    )


class InferenceRelay:
    def __init__(
        self,
        config: RelayConfig,
        authorize: Authorizer,
        client: httpx.AsyncClient | None = None,
    ):
        self.config = config
        self._authorize = authorize
        self.client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(config.read_timeout, connect=config.connect_timeout),
            transport=httpx.AsyncHTTPTransport(retries=0),
            trust_env=False,
        )
        self._inflight = 0
        self._user_inflight: dict[str, int] = {}
        self._waiting = 0

    def _acquire(self, user_id: str) -> Callable[[], None] | None:
        held = self._user_inflight.get(user_id, 0)
        if (
            self._inflight >= self.config.max_inflight
            or held >= self.config.max_inflight_per_user
        ):
            return None
        self._inflight += 1
        self._user_inflight[user_id] = held + 1
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._inflight -= 1
            left = self._user_inflight[user_id] - 1
            if left:
                self._user_inflight[user_id] = left
            else:
                del self._user_inflight[user_id]

        return release

    def _validate_and_build_upstream(
        self,
        rid: str,
        gateway: GatewayProfile,
        res: Any,
        alias: str,
        body: dict[str, Any],
    ) -> Response | httpx.Request:
        """Re-run on fresh (gateway, res) after a capacity wait, so a stale
        catalog/policy/key resolved before the wait never reaches upstream."""
        if alias not in gateway.models:
            return _deny(403, 'model_not_in_catalog')
        if alias not in res.allowed_models:
            return _deny(403, 'model_not_permitted')
        if body.get('stream') and 'stream' not in gateway.capabilities:
            return _deny(400, 'capability_stream_unsupported')
        if (
            'tools' in body or 'tool_choice' in body
        ) and 'tools' not in gateway.capabilities:
            return _deny(400, 'capability_tools_unsupported')
        upstream_body = {**body, 'model': gateway.models[alias]}
        return self.client.build_request(
            'POST',
            gateway.chat_completions_url(),
            content=json.dumps(upstream_body).encode(),
            headers={
                **gateway.credential_headers(res.upstream_key),
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream'
                if body.get('stream')
                else 'application/json',
                'Accept-Encoding': 'identity',
                'X-OpenHands-Request-Id': rid,
            },
        )

    async def _authorize_or_deny(
        self, request: Request, rid: str, *, timeout: float | None = None
    ) -> tuple[Any, GatewayProfile, Any] | Response:
        """Shared by the initial authorization and the post-wait reauthorization:
        the same typed-error -> response mapping either way. With ``timeout`` set,
        bounds the lookup so a request waiting for capacity cannot hold a slot
        through another long runtime-API call."""
        try:
            ctx = anyio.fail_after(timeout) if timeout else contextlib.nullcontext()
            with ctx:
                return await self._authorize(request)
        except TimeoutError:
            logger.info(
                'relay_deny', extra={'rid': rid, 'reason': 'sandbox_status_unknown'}
            )
            return _deny(503, 'sandbox_status_unknown', {'retry-after': '1'})
        except MissingInferenceToken:
            # Typed, not a check on the exact message raised in
            # service.authorize_inference: a standalone SDK client gets this
            # explicit error instead of the generic invalid_token below.
            return JSONResponse(
                {
                    'error': {
                        'type': 'relay_denied',
                        'code': 'missing_inference_token',
                        'message': MISSING_TOKEN_MESSAGE,
                    }
                },
                status_code=401,
            )
        except InvalidInferenceToken:
            logger.info('relay_deny', extra={'rid': rid, 'reason': 'invalid_token'})
            return _deny(401, 'invalid_token')
        except Denied as d:
            logger.info('relay_deny', extra={'rid': rid, 'reason': d.reason})
            return _deny(403, d.reason)
        except CustomerGatewaySandboxLookupUnavailable:
            # Retryable: the sandbox's live status is unknown right now (e.g. a
            # transient runtime-API error), not confirmed inactive.
            logger.info(
                'relay_deny', extra={'rid': rid, 'reason': 'sandbox_status_unknown'}
            )
            return _deny(503, 'sandbox_status_unknown', {'retry-after': '1'})

    def routes(self) -> list[Route]:
        return [
            Route('/v1/chat/completions', self.chat_completions, methods=['POST']),
            Route('/v1/models', self.models, methods=['GET']),
        ]

    async def models(self, request: Request) -> Response:
        try:
            _, gateway, res = await self._authorize(request)
        except MissingInferenceToken:
            return JSONResponse(
                {
                    'error': {
                        'type': 'relay_denied',
                        'code': 'missing_inference_token',
                        'message': MISSING_TOKEN_MESSAGE,
                    }
                },
                status_code=401,
            )
        except InvalidInferenceToken:
            return _deny(401, 'invalid_token')
        except Denied as d:
            return _deny(403, d.reason)
        except CustomerGatewaySandboxLookupUnavailable:
            # Retryable: the sandbox's live status is unknown right now (e.g. a
            # transient runtime-API error), not confirmed inactive.
            return _deny(503, 'sandbox_status_unknown', {'retry-after': '1'})
        ids = sorted(m for m in gateway.models if m in res.allowed_models)
        return JSONResponse(
            {'object': 'list', 'data': [{'id': m, 'object': 'model'} for m in ids]}
        )

    async def chat_completions(self, request: Request) -> Response:
        rid = uuid.uuid4().hex
        auth_result = await self._authorize_or_deny(request, rid)
        if isinstance(auth_result, Response):
            return auth_result
        principal, gateway, res = auth_result

        if request.query_params:
            return _deny(400, 'query_not_supported')
        cl = request.headers.get('content-length')
        if cl is not None and not cl.isdigit():
            return _deny(400, 'invalid_content_length')
        if cl and int(cl) > self.config.max_body_bytes:
            return _deny(413, 'body_too_large')
        raw = bytearray()
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > self.config.max_body_bytes:
                return _deny(413, 'body_too_large')
        try:
            body = json.loads(raw)
        except ValueError:
            return _deny(400, 'invalid_json')
        if not isinstance(body, dict):
            return _deny(400, 'invalid_json')
        extra = sorted(set(body) - ALLOWED_BODY_KEYS)
        if extra:
            return JSONResponse(
                {
                    'error': {
                        'type': 'relay_denied',
                        'code': 'forbidden_body_fields',
                        'fields': extra,
                    }
                },
                status_code=400,
            )
        alias = body.get('model')
        if not isinstance(alias, str):
            return _deny(400, 'invalid_model')

        release = self._acquire(principal.user_id)
        if release is None:
            # One short, bounded wait for capacity to recover (e.g. an
            # in-flight request finishing) before denying. No upstream call is
            # made during the wait and this is not a queue: at most
            # config.max_inflight requests wait at a time (an in-process
            # counter, not an unbounded queue), each for exactly one bounded
            # sleep, then a 429 if the process is still at its
            # per-member/global cap.
            if self._waiting >= self.config.max_inflight:
                return JSONResponse(
                    {'error': {'type': 'relay_denied', 'code': 'relay_capacity'}},
                    status_code=429,
                    headers={'retry-after': '1'},
                )
            self._waiting += 1
            try:
                await anyio.sleep(CAPACITY_WAIT_S)
                # Authorization (sandbox liveness, membership, token/policy, key)
                # was resolved before the wait; a revoked/rotated key, a changed
                # policy or a sandbox that stopped while we waited must not be
                # allowed through on stale state. This runs before the second
                # `_acquire`, still inside the `_waiting` accounting and before
                # any slot is held, so a cancellation here (e.g. the client
                # disconnecting) cannot leak a forwarding slot. The deadline
                # matches CAPACITY_WAIT_S so an overload request cannot hold a
                # waiter slot through another long runtime-API lookup.
                auth_result = await self._authorize_or_deny(
                    request, rid, timeout=CAPACITY_WAIT_S
                )
            finally:
                self._waiting -= 1
            if isinstance(auth_result, Response):
                return auth_result
            principal, gateway, res = auth_result
            release = self._acquire(principal.user_id)
            if release is None:
                return JSONResponse(
                    {'error': {'type': 'relay_denied', 'code': 'relay_capacity'}},
                    status_code=429,
                    headers={'retry-after': '1'},
                )

        # Built once, from the final (possibly reauthorized) snapshot of
        # gateway/res -- never from a snapshot taken before the wait. Any
        # exception here (e.g. a configured header that httpx rejects) must
        # still release the slot just acquired above.
        try:
            result = self._validate_and_build_upstream(rid, gateway, res, alias, body)
        except BaseException:
            release()
            raise
        if isinstance(result, Response):
            release()
            return result
        upstream = result

        audit = {
            'rid': rid,
            'user': principal.user_id,
            'org': principal.org_id,
            'sid': principal.session_id,
            'jti': principal.jti,
            'model': alias,
            'key_fp': res.fingerprint,
            'key_ver': res.credential_version,
        }
        try:
            resp = await self.client.send(upstream, stream=True)
        except BaseException as exc:
            release()
            if not isinstance(exc, httpx.HTTPError):
                raise
            logger.warning(
                'relay_upstream_error', extra={**audit, 'error': type(exc).__name__}
            )
            return _deny(
                504 if isinstance(exc, httpx.TimeoutException) else 502,
                'upstream_unreachable',
            )
        logger.info('relay_forward', extra={**audit, 'status': resp.status_code})
        if resp.is_redirect:
            await _close(resp)
            release()
            return _deny(424, 'upstream_redirect_refused')
        if resp.status_code == 401:
            # Relay 401 is reserved for inference-token problems: the SDK refreshes on 401,
            # and a rejected customer key must not trigger a pointless token refresh.
            await _close(resp)
            release()
            return _deny(424, 'customer_gateway_key_rejected')
        if not resp.is_success:
            try:
                return await _guarded_error(resp, res.upstream_key, rid)
            finally:
                release()
        headers = {h: resp.headers[h] for h in PASSBACK_HEADERS if h in resp.headers}
        headers['x-openhands-request-id'] = rid
        return OwnedStreamingResponse(
            resp, status_code=resp.status_code, headers=headers, on_close=release
        )


async def _close(resp: httpx.Response) -> None:
    with anyio.CancelScope(shield=True), anyio.move_on_after(CLOSE_TIMEOUT_S):
        await resp.aclose()


class OwnedStreamingResponse(StreamingResponse):
    """Owns the upstream response: closed even if the body iterator never starts or is cancelled."""

    def __init__(self, upstream: httpx.Response, on_close=None, **kwargs):
        self.upstream = upstream
        self.on_close = on_close
        super().__init__(upstream.aiter_raw(), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await _close(self.upstream)
            if self.on_close:
                self.on_close()


async def _guarded_error(resp: httpx.Response, key: str, rid: str) -> Response:
    """Preserve bounded errors while removing exact key material; successful output is trusted."""
    body = bytearray()
    oversized = False
    try:
        async for chunk in resp.aiter_bytes(chunk_size=4096):
            if len(body) + len(chunk) > ERROR_BODY_LIMIT:
                oversized = True
                break
            body.extend(chunk)
    finally:
        await _close(resp)
    headers = {
        h: resp.headers[h] for h in ('content-type', 'retry-after') if h in resp.headers
    }
    headers['x-openhands-request-id'] = rid
    if oversized:
        headers.pop('content-type', None)
        return JSONResponse(
            {
                'error': {
                    'type': 'upstream_error',
                    'message': 'Customer gateway error exceeded the relay size limit',
                }
            },
            status_code=resp.status_code,
            headers=headers,
        )
    # Normalize valid JSON before exact matching so JSON-escaped ASCII keys cannot bypass redaction.
    try:
        normalized = json.dumps(json.loads(body)).encode()
    except (ValueError, RecursionError):
        normalized = bytes(body)
    return Response(
        normalized.replace(key.encode(), b'[redacted]'),
        status_code=resp.status_code,
        headers=headers,
    )
