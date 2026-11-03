"""Strict, idempotent external cleanup for administrator lifecycle requests."""

import httpx
from keycloak.exceptions import KeycloakError

from server.auth.constants import KEYCLOAK_SERVER_URL
from server.auth.keycloak_manager import get_keycloak_admin
from server.constants import LITE_LLM_API_KEY, LITE_LLM_API_URL
from storage.lite_llm_manager import is_litellm_enabled


def _uses_keycloak(idp_user: bool) -> bool:
    """Whether the user has a Keycloak identity to update.

    A service account never has one. Without ``KEYCLOAK_SERVER_URL`` (integrated
    login only) no user does.
    """
    return idp_user and bool(KEYCLOAK_SERVER_URL)


class UserLifecycleRemote:
    """External steps of ``AdminUserLifecycleService``: Keycloak and LiteLLM.

    ``idp_user`` is False for a service account, which has LiteLLM state but no
    Keycloak user.
    """

    async def disable(self, user_id: str, *, idp_user: bool = True) -> None:
        if _uses_keycloak(idp_user):
            admin = get_keycloak_admin()
            try:
                await admin.a_update_user(user_id, {'enabled': False})
                await admin.a_user_logout(user_id)
            except KeycloakError as exc:
                if exc.response_code != 404:
                    raise
        # Unlike the general get_user_keys helper, a failed listing must not
        # look like an empty list and silently claim that revocation succeeded.
        if await is_litellm_enabled() and LITE_LLM_API_URL and LITE_LLM_API_KEY:
            async with httpx.AsyncClient(
                headers={'x-goog-api-key': LITE_LLM_API_KEY}, timeout=30
            ) as client:
                keys = []
                page = 1
                while True:
                    response = await client.get(
                        f'{LITE_LLM_API_URL}/key/list',
                        params={'user_id': user_id, 'page': page, 'size': 100},
                    )
                    if response.status_code == 404:
                        break
                    response.raise_for_status()
                    data = response.json()
                    keys.extend(data['keys'])
                    if page >= data['total_pages']:
                        break
                    page += 1
                for offset in range(0, len(keys), 100):
                    response = await client.post(
                        f'{LITE_LLM_API_URL}/key/delete',
                        json={'keys': keys[offset : offset + 100]},
                    )
                    if response.status_code != 404:
                        response.raise_for_status()

    async def enable(self, user_id: str, *, idp_user: bool = True) -> None:
        if not _uses_keycloak(idp_user):
            return
        try:
            await get_keycloak_admin().a_update_user(user_id, {'enabled': True})
        except KeycloakError as exc:
            if exc.response_code != 404:
                raise

    async def delete(self, user_id: str, *, idp_user: bool = True) -> None:
        if await is_litellm_enabled() and LITE_LLM_API_URL and LITE_LLM_API_KEY:
            async with httpx.AsyncClient(
                headers={'x-goog-api-key': LITE_LLM_API_KEY}, timeout=30
            ) as client:
                response = await client.post(
                    f'{LITE_LLM_API_URL}/user/delete', json={'user_ids': [user_id]}
                )
                if response.status_code != 404:
                    response.raise_for_status()
        if not _uses_keycloak(idp_user):
            return
        try:
            await get_keycloak_admin().a_delete_user(user_id)
        except KeycloakError as exc:
            if exc.response_code != 404:
                raise
