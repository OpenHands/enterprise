from __future__ import annotations

from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from server.services.org_migration_service import MIGRATION_TYPES

MigrationType = Literal['secrets', 'keys', 'mcp', 'automations']


class OrgMigrationSource(BaseModel):
    mode: Literal['personal', 'org']
    org_id_or_name: str | None = None

    @model_validator(mode='after')
    def _validate_source(self):
        if self.mode == 'org' and not self.org_id_or_name:
            raise ValueError('source.org_id_or_name is required when mode is org')
        if self.mode == 'personal' and self.org_id_or_name:
            raise ValueError(
                'source.org_id_or_name must be omitted when mode is personal'
            )
        return self


class OrgMigrationRequest(BaseModel):
    source: OrgMigrationSource
    users: list[str] | None = None
    all: bool = False
    types: list[MigrationType] = Field(
        default_factory=lambda: cast(list[MigrationType], list(MIGRATION_TYPES))
    )
    dry_run: bool = False

    @model_validator(mode='after')
    def _validate_users(self):
        if self.all and self.users:
            raise ValueError('Provide either all=true or a users list, not both')
        if not self.all and not self.users:
            raise ValueError('Provide users or set all=true')
        return self


class OrgMigrationUserResult(BaseModel):
    user_id: str
    email: str | None
    source_org_id: UUID
    target_org_id: UUID
    actions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class OrgMigrationResponse(BaseModel):
    dry_run: bool
    results: list[OrgMigrationUserResult]
    missing_identifiers: list[str] = Field(default_factory=list)
