"""Keycloak broker compatibility for existing provider connections only."""

from uuid import UUID

from pydantic import SecretStr

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.contracts import AuthenticationUnavailable, InvalidCredentials
from server.auth.mode import is_keycloak_enabled


async def resolve_broker_actor(provider: ProviderType, account_id: str) -> UUID | None:
    if not is_keycloak_enabled():
        return None
    from server.auth.keycloak.manager import get_keycloak_admin

    try:
        users = await get_keycloak_admin().a_get_users(
            {'q': f'{provider.value}_id:{account_id}', 'briefRepresentation': False}
        )
    except Exception as exc:
        raise AuthenticationUnavailable(
            'Provider identity lookup is temporarily unavailable'
        ) from exc
    # Never guess between multiple accounts or use email as an identity link.
    exact = [
        user
        for user in users
        if account_id in (user.get('attributes') or {}).get(f'{provider.value}_id', [])
    ]
    if len(exact) != 1:
        return None
    identity = exact[0]
    if identity.get('enabled') is False:
        raise InvalidCredentials('Account is unavailable')
    return await _hydrate_proven_subject(
        identity.get('id'),
        {
            **identity,
            'email_verified': identity.get('emailVerified', False),
        },
    )


async def user_from_broker_token(access_token: SecretStr) -> str:
    if not is_keycloak_enabled():
        raise InvalidCredentials('Broker authentication is unavailable')
    from server.auth.token_manager import TokenManager

    user = await TokenManager().get_user_info(access_token.get_secret_value())
    return str(
        await _hydrate_proven_subject(user.sub, user.model_dump(exclude_none=True))
    )


async def _hydrate_proven_subject(subject: str | None, user_info: dict) -> UUID:
    from server.auth.user_management import EnterpriseUserManagementService

    try:
        user_id = UUID(str(subject))
    except ValueError:
        raise InvalidCredentials('Invalid account identifier') from None
    account = await EnterpriseUserManagementService().ensure_authenticated_account(
        user_id, user_info
    )
    if account is None or account.is_disabled:
        raise InvalidCredentials('Account is unavailable')
    return user_id


async def unlink_broker_provider(user_id: str, provider: ProviderType) -> None:
    if not is_keycloak_enabled():
        return
    from keycloak.exceptions import KeycloakDeleteError
    from server.auth.keycloak.manager import get_keycloak_admin

    try:
        await get_keycloak_admin().a_delete_user_social_login(user_id, provider.value)
    except KeycloakDeleteError as exc:
        if exc.response_code != 404:
            raise AuthenticationUnavailable(
                'Provider disconnect is temporarily unavailable'
            ) from exc
    except Exception as exc:
        raise AuthenticationUnavailable(
            'Provider disconnect is temporarily unavailable'
        ) from exc


async def resolve_broker_email(email: str) -> str | None:
    """Preserve explicitly enabled legacy Jira email mode on KC deployments.

    Local authentication requires the integration's verified account link.
    """
    if not is_keycloak_enabled():
        return None
    from server.auth.token_manager import TokenManager

    return await TokenManager().get_user_id_from_user_email(email)
