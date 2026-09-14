"""Explicit contracts shared by the native authentication test fixtures."""

from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypedDict, TypeVar, overload
from unittest.mock import Mock
from uuid import UUID

from fastapi import FastAPI

from server.auth.native_types import SessionFactory
from server.services.native_auth_service import NativeAuthService, NativeLogin
from server.services.native_saml_service import NativeSamlService
from storage.user import User

NativeFixture = tuple[NativeAuthService, UUID]
NativeRuntime = tuple[NativeAuthService, NativeLogin, str]
SamlRuntime = tuple[FastAPI, NativeSamlService, NativeAuthService, NativeLogin]
SigningMaterial = tuple[str, str]


class CreateUser(Protocol):
    def __call__(self, *, email: str) -> User: ...


@dataclass(frozen=True)
class ConfiguredSamlIdentity:
    connection_id: str
    issuer: str


@dataclass(frozen=True)
class DisabledNative:
    sessions: SessionFactory
    service: NativeAuthService
    admin_id: UUID
    http: Mock


class FederatedOptions(TypedDict, total=False):
    return_path: str | None
    invitation_token: str | None
    link_account_id: UUID | None
    link_session_id: UUID | None
    link_session_token: str | None
    auth_method: str
    auth_time: datetime | None
    session_expiry_bound: datetime | None


class FederatedClaims(TypedDict):
    connection_id: str
    issuer: str
    subject: str
    email: str
    allow_jit: bool


class SamlStartBody(TypedDict, total=False):
    return_path: str
    invitation_token: str
    link: bool
    reauthenticate: bool


class XmlElement(Protocol):
    """Mutable lxml element operations used to build real protocol fixtures."""

    text: str | None

    @property
    def attrib(self) -> MutableMapping[str, str]: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator['XmlElement']: ...
    @overload
    def get(self, key: str, default: None = None) -> str | None: ...
    @overload
    def get(self, key: str, default: str) -> str: ...
    def find(
        self, path: str, namespaces: Mapping[str, str] | None = None
    ) -> 'XmlElement | None': ...
    def findall(
        self, path: str, namespaces: Mapping[str, str] | None = None
    ) -> list['XmlElement']: ...
    def findtext(
        self,
        path: str,
        default: None = None,
        namespaces: Mapping[str, str] | None = None,
    ) -> str | None: ...
    def append(self, element: 'XmlElement') -> None: ...
    def remove(self, element: 'XmlElement') -> None: ...
    def replace(self, old_element: 'XmlElement', new_element: 'XmlElement') -> None: ...
    def set(self, key: str, value: str) -> None: ...


T = TypeVar('T')


def present(value: T | None) -> T:
    """Assert a fixture or database result required by the behavior under test."""
    assert value is not None
    return value
