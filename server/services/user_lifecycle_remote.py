"""Strict, idempotent external cleanup for administrator lifecycle requests."""

import httpx
from keycloak.exceptions import KeycloakError

from server.auth.keycloak_manager import get_keycloak_admin
from server.constants import LITE_LLM_API_KEY, LITE_LLM_API_URL


class UserLifecycleRemote:
    async def disable(self, user_id: str) -> None:
        admin = get_keycloak_admin()
        try:
            await admin.a_update_user(user_id, {'enabled': False})
            await admin.a_user_logout(user_id)
        except KeycloakError as exc:
            if exc.response_code != 404:
                raise
        # Unlike the general get_user_keys helper, a failed listing must not
        # look like an empty list and silently claim that revocation succeeded.
        if LITE_LLM_API_URL and LITE_LLM_API_KEY:
            async with httpx.AsyncClient(
                headers={'x-goog-api-key': LITE_LLM_API_KEY}, timeout=30
            ) as client:
                response = await client.get(
                    f'{LITE_LLM_API_URL}/key/list', params={'user_id': user_id}
                )
                if response.status_code == 404:
                    return
                response.raise_for_status()
                keys = response.json()['keys']
                if keys:
                    response = await client.post(
                        f'{LITE_LLM_API_URL}/key/delete', json={'keys': keys}
                    )
                    if response.status_code != 404:
                        response.raise_for_status()

    async def enable(self, user_id: str) -> None:
        await get_keycloak_admin().a_update_user(user_id, {'enabled': True})

    async def delete(self, user_id: str) -> None:
        if LITE_LLM_API_URL and LITE_LLM_API_KEY:
            async with httpx.AsyncClient(
                headers={'x-goog-api-key': LITE_LLM_API_KEY}, timeout=30
            ) as client:
                response = await client.post(
                    f'{LITE_LLM_API_URL}/user/delete', json={'user_ids': [user_id]}
                )
                if response.status_code != 404:
                    response.raise_for_status()
        try:
            await get_keycloak_admin().a_delete_user(user_id)
        except KeycloakError as exc:
            if exc.response_code != 404:
                raise
