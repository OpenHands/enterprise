"""Explicit contracts shared by the native authentication test fixtures."""

from dataclasses import dataclass
from typing import Protocol, TypeVar
from unittest.mock import Mock
from uuid import UUID

import httpx
from pydantic import BaseModel

from server.auth.native_types import SessionFactory
from server.services.native_auth_service import NativeAuthService, NativeLogin
from storage.user import User

NativeFixture = tuple[NativeAuthService, UUID]
NativeRuntime = tuple[NativeAuthService, NativeLogin, str]


class CreateUser(Protocol):
    def __call__(self, *, email: str) -> User: ...


@dataclass(frozen=True)
class DisabledNative:
    sessions: SessionFactory
    service: NativeAuthService
    admin_id: UUID
    http: Mock


T = TypeVar('T')


class CsrfResponse(BaseModel):
    csrf_token: str


async def get_csrf_token(client: httpx.AsyncClient) -> str:
    response = await client.get('/api/auth/csrf')
    assert response.status_code == 200
    return CsrfResponse.model_validate_json(response.content).csrf_token


def present(value: T | None) -> T:
    """Assert a fixture or database result required by the behavior under test."""
    assert value is not None
    return value
