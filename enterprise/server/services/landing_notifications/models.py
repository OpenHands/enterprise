from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Environment(StrEnum):
    SAAS_STAGING = 'saas-staging'
    SAAS_PRODUCTION = 'saas-production'
    REPLICATED_UNSTABLE = 'replicated-unstable'
    REPLICATED_BETA = 'replicated-beta'
    REPLICATED_STABLE = 'replicated-stable'


class LandingStage(StrEnum):
    REVIEW = 'review'
    MERGED = 'merged'
    TESTABLE = 'testable'
    BUG_BASH = 'bug-bash'
    COUNCIL_REVIEW = 'council-review'
    COUNCIL_APPROVED = 'council-approved'
    PRODUCTION_ENABLED = 'production-enabled'
    GA = 'ga'


class DeliveryPolicy(BaseModel):
    test_targets: frozenset[Environment]
    final_targets: frozenset[Environment]

    @model_validator(mode='after')
    def require_targets(self) -> 'DeliveryPolicy':
        if not self.test_targets:
            raise ValueError('test_targets must contain at least one environment')
        if not self.final_targets:
            raise ValueError('final_targets must contain at least one environment')
        return self


class ReleaseArtifact(BaseModel):
    kind: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sequence: int | None = Field(default=None, ge=1)
    kots_cursor: int | None = Field(default=None, ge=1)


class ReleaseComponent(BaseModel):
    repo: str = Field(pattern=r'^[^/\s]+/[^/\s]+$')
    previous_ref: str = Field(min_length=1)
    released_ref: str = Field(min_length=1)


class EnvironmentRelease(BaseModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    environment: Environment
    status: Literal['ready'] = 'ready'
    released_at: datetime
    producer_repo: str = Field(pattern=r'^[^/\s]+/[^/\s]+$')
    producer_sha: str = Field(pattern=r'^[0-9a-f]{40}$')
    run_url: str = Field(min_length=1)
    environment_url: str = Field(min_length=1)
    artifact: ReleaseArtifact
    components: tuple[ReleaseComponent, ...] = Field(min_length=1)

    @model_validator(mode='after')
    def require_unique_component_repos(self) -> 'EnvironmentRelease':
        repos = [component.repo for component in self.components]
        if len(repos) != len(set(repos)):
            raise ValueError('components must contain each repository at most once')
        return self


class ReleaseEvidence(BaseModel):
    environment: Environment
    event_id: str = Field(min_length=1)
    artifact_version: str = Field(min_length=1)
    environment_url: str = Field(min_length=1)
    run_url: str = Field(min_length=1)
    released_at: datetime


class FeatureProgress(BaseModel):
    merged: bool = False
    bug_bash_started: bool = False
    bug_bash_complete: bool = False
    council_approved: bool = False
    flag_verified: bool = False
    ga_complete: bool = False
    evidence: dict[Environment, ReleaseEvidence] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_progress_order(self) -> 'FeatureProgress':
        if self.bug_bash_complete and not self.bug_bash_started:
            raise ValueError('bug_bash_complete requires bug_bash_started')
        if self.council_approved and not self.bug_bash_complete:
            raise ValueError('council_approved requires bug_bash_complete')
        if self.ga_complete and not self.flag_verified:
            raise ValueError('ga_complete requires flag_verified')
        return self
