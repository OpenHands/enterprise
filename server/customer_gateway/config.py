"""Deployment switch for the opt-in customer gateway. OFF unless CUSTOMER_GATEWAY_ENABLED=true;
when OFF nothing is mounted and launch/sandbox hooks return before any DB access."""

import os
from dataclasses import dataclass
from datetime import timedelta

from server.customer_gateway.tokens import MAX_TTL


class CustomerGatewayConfigError(ValueError):
    """Deployment misconfiguration caught at startup, not at the first gateway
    launch -- so a bad CUSTOMER_GATEWAY_* env var fails loudly here instead of
    surfacing later as a bare ValueError out of mint_inference_token."""


@dataclass(frozen=True)
class CustomerGatewaySettings:
    relay_url: str  # base_url sandboxes use for LLM calls (ends with /v1)
    token_url: str  # sandbox-authenticated inference-token refresh URL
    token_ttl: timedelta
    allow_http: bool  # isolated test networks only


def env_flag(name: str) -> bool:
    return os.environ.get(name, '').strip().lower() in ('1', 'true')


def get_settings() -> CustomerGatewaySettings | None:
    if not env_flag('CUSTOMER_GATEWAY_ENABLED'):
        return None
    relay_url = os.environ['CUSTOMER_GATEWAY_RELAY_URL'].rstrip('/')
    if not relay_url.endswith('/v1'):
        # Never echo the configured value: a mistyped URL may carry userinfo
        # (https://user:pass@host/...), which would otherwise land in logs.
        raise CustomerGatewayConfigError('CUSTOMER_GATEWAY_RELAY_URL must end with /v1')
    raw_ttl = os.environ.get('CUSTOMER_GATEWAY_TOKEN_TTL_SECONDS', '900')
    try:
        ttl_seconds = int(raw_ttl)
    except ValueError:
        # Typed error, and don't echo the raw env text either.
        raise CustomerGatewayConfigError(
            'CUSTOMER_GATEWAY_TOKEN_TTL_SECONDS must be an integer number of seconds'
        ) from None
    if not 0 < ttl_seconds <= MAX_TTL.total_seconds():
        raise CustomerGatewayConfigError(
            'CUSTOMER_GATEWAY_TOKEN_TTL_SECONDS must be in '
            f'(0, {int(MAX_TTL.total_seconds())}] seconds (mint_inference_token '
            'enforces the same MAX_TTL at mint time)'
        )
    return CustomerGatewaySettings(
        relay_url=relay_url,
        token_url=os.environ['CUSTOMER_GATEWAY_TOKEN_URL'],
        token_ttl=timedelta(seconds=ttl_seconds),
        allow_http=env_flag('CUSTOMER_GATEWAY_ALLOW_HTTP_TEST_ONLY'),
    )
