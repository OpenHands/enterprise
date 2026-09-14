"""Accurate shared fixtures for gateway and SaaS regression tests."""

from collections.abc import Callable
from typing import TypedDict
from uuid import UUID

import httpx
from httpx._client import UseClientDefault
from pydantic import SecretStr


class OrgMembersFixture(TypedDict):
    org_id: UUID
    admin_user_id: UUID
    member1_user_id: UUID
    member2_user_id: UUID
    decrypt_value: Callable[[str | SecretStr], str]


class HttpxSendOptions(TypedDict, total=False):
    stream: bool
    auth: (
        httpx.Auth
        | tuple[str, str]
        | Callable[[httpx.Request], httpx.Request]
        | UseClientDefault
        | None
    )
    follow_redirects: bool | UseClientDefault
