"""Deployment switch for the opt-in customer gateway. OFF unless CUSTOMER_GATEWAY_ENABLED=true;
when OFF nothing is mounted and launch/sandbox hooks return before any DB access."""

import os
from dataclasses import dataclass
from datetime import timedelta


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
    return CustomerGatewaySettings(
        relay_url=os.environ['CUSTOMER_GATEWAY_RELAY_URL'].rstrip('/'),
        token_url=os.environ['CUSTOMER_GATEWAY_TOKEN_URL'],
        token_ttl=timedelta(
            seconds=int(os.environ.get('CUSTOMER_GATEWAY_TOKEN_TTL_SECONDS', '900'))
        ),
        allow_http=env_flag('CUSTOMER_GATEWAY_ALLOW_HTTP_TEST_ONLY'),
    )
