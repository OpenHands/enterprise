"""Explicit contracts shared by the native authentication test fixtures."""

from dataclasses import dataclass
from typing import Protocol, TypeVar
from unittest.mock import Mock
from uuid import UUID

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


def present(value: T | None) -> T:
    """Assert a fixture or database result required by the behavior under test."""
    assert value is not None
    return value
