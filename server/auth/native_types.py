"""Typed native-auth database and public-response contracts."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class InvitationLink(TypedDict):
    invitation_id: str
    invite_url: str
    expires_at: datetime


class PasswordResetLink(TypedDict):
    reset_url: str
    expires_at: datetime


class NativeProfileMetadata(TypedDict):
    email: str | None
    has_password: bool
    authentication_methods: list[str]


class InvitationInspection(TypedDict):
    email: str
    org_id: UUID | None
    org_name: str | None
    org_role_id: int | None
    expires_at: datetime
    action: Literal['login', 'set_password']
    authentication_methods: list[str]


class InvitationMetadata(TypedDict):
    id: str
    email: str
    org_id: UUID | None
    org_role_id: int | None
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None
    accepted_account_id: UUID | None


class InvitationPage(TypedDict):
    items: list[InvitationMetadata]
    total: int


class InvitationOrganization(TypedDict):
    id: str
    name: str


class InvitationOrganizationPage(TypedDict):
    items: list[InvitationOrganization]
    total: int


class NativeAccountMetadata(TypedDict):
    id: str
    email: str | None
    authentication_methods: list[str]
    state: str
    profile_present: bool
    is_disabled: bool
    role_id: int | None
    created_at: datetime
    pending_invitations: list[InvitationMetadata]


class NativeAccountPage(TypedDict):
    items: list[NativeAccountMetadata]
    total: int


class GitProviderCapability(TypedDict):
    methods: list[str]
    hosts: list[str]
    default_host: str
    webhook_host: NotRequired[str]
    installation_available: NotRequired[bool]


class GitAccountView(TypedDict):
    id: str | None
    login: str | None
    display_name: str | None
    avatar_url: str | None


class GitConnectionView(TypedDict):
    provider: str
    host: str
    auth_type: str
    status: Literal['reconnect_required', 'connected']
    account: GitAccountView
    last_error: str | None


class GitConnectionList(TypedDict):
    connections: list[GitConnectionView]
    capabilities: dict[str, GitProviderCapability]
