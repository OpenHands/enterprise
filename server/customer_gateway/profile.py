"""Gateway-neutral upstream profile: one fixed origin, credential placement, catalog, capabilities.

Only the OpenAI chat-completions protocol is implemented. A gateway product (LiteLLM today) is
described by profile values, not code. Other products are not claimed as supported.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

PROTOCOLS = frozenset({'openai_chat_completions'})
CAPABILITIES = frozenset({'stream', 'tools'})
_FORBIDDEN_HEADERS = frozenset(
    {'host', 'content-length', 'content-type', 'transfer-encoding', 'cookie'}
)


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class GatewayProfile:
    base_url: str
    models: dict[str, str]  # catalog alias -> upstream model name
    protocol: str = 'openai_chat_completions'
    credential_header: str = 'authorization'  # 'authorization' => "Bearer <key>"; else raw key in that header
    capabilities: frozenset[str] = CAPABILITIES

    def credential_headers(self, key: str) -> dict[str, str]:
        if self.credential_header == 'authorization':
            return {'Authorization': f'Bearer {key}'}
        return {self.credential_header: key}

    def chat_completions_url(self) -> str:
        return self.base_url.rstrip('/') + '/chat/completions'


def parse_profile(raw: dict, *, allow_http: bool = False) -> GatewayProfile:
    """Validate admin-supplied profile. allow_http is for isolated test networks only."""
    url = urlsplit(str(raw.get('base_url', '')))
    if url.scheme != 'https' and not (allow_http and url.scheme == 'http'):
        raise ProfileError('base_url must be https')
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ProfileError('base_url must be a plain origin+path')
    protocol = raw.get('protocol', 'openai_chat_completions')
    if protocol not in PROTOCOLS:
        raise ProfileError(f'unsupported protocol {protocol!r}')
    header = str(raw.get('credential_header', 'authorization')).lower()
    if header in _FORBIDDEN_HEADERS or not header.replace('-', '').isalnum():
        raise ProfileError('invalid credential_header')
    caps = frozenset(raw.get('capabilities', sorted(CAPABILITIES)))
    if not caps <= CAPABILITIES:
        raise ProfileError(f'unknown capabilities {sorted(caps - CAPABILITIES)}')
    models = raw.get('models')
    if (
        not isinstance(models, dict)
        or not models
        or not all(
            isinstance(k, str) and isinstance(v, str) and k and v
            for k, v in models.items()
        )
    ):
        raise ProfileError('models must be a non-empty alias->model map')
    return GatewayProfile(url.geturl(), dict(models), protocol, header, caps)
