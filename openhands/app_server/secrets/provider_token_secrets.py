"""The OAuth / git provider token secrets injected into a conversation sandbox."""

from dataclasses import dataclass
from typing import cast

from openhands.app_server.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    ProviderHandler,
)
from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.user.user_context import UserContext
from openhands.app_server.user_auth.user_auth import GitProviderSelection


@dataclass(frozen=True)
class ProviderTokenSecretSpec:
    """Identity of one injected token secret. Never carries a token value.

    Exactly one of ``provider_type`` (a ``<PROVIDER>_TOKEN`` secret) and
    ``oauth_provider_id`` (a named OAuth secret) is set.
    """

    name: str
    description: str
    provider_type: ProviderType | None = None
    oauth_provider_id: int | None = None
    # The connected provider of ``provider_type`` the token is read from.
    git_provider_id: int | None = None


async def get_provider_token_secret_specs(
    user_context: UserContext, git_provider_id: int | None = None
) -> list[ProviderTokenSecretSpec]:
    """Name the token secrets a new sandbox gets for the user.

    With named OAuth secrets in use, each linked provider's token is exposed
    under its ``named_secret``; ``<PROVIDER_TYPE>_TOKEN`` remains only for
    provider types the user has no OAuth v2 token for.

    With ``git_provider_id``, the connected provider it names supplies the token
    of its type, and wins the name of its ``named_secret``.
    """
    specs: list[ProviderTokenSecretSpec] = []
    provider_tokens = cast(
        PROVIDER_TOKEN_TYPE | None,
        await user_context.get_provider_tokens(git_provider_id=git_provider_id),
    )
    named_oauth_secrets = await user_context.get_named_oauth_secrets(git_provider_id)
    selection = (
        await user_context.select_git_provider(None, git_provider_id)
        if git_provider_id is not None
        else GitProviderSelection()
    )

    for provider_type, provider_token in (provider_tokens or {}).items():
        if not provider_token.token:
            continue
        if (
            named_oauth_secrets is not None
            and provider_type in named_oauth_secrets.linked_provider_types
        ):
            continue
        specs.append(
            ProviderTokenSecretSpec(
                name=ProviderHandler.get_provider_env_key(provider_type),
                description=f'{provider_type.name} authentication token',
                provider_type=provider_type,
                git_provider_id=(
                    selection.git_provider_id
                    if provider_type == selection.git_provider
                    else None
                ),
            )
        )

    for named in named_oauth_secrets.secrets if named_oauth_secrets else ():
        specs.append(
            ProviderTokenSecretSpec(
                name=named.secret_name,
                description=named.description,
                oauth_provider_id=named.oauth_provider_id,
            )
        )
    return specs
