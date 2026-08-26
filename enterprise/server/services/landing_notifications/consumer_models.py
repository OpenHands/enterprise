from pydantic import BaseModel, Field
from server.services.landing_notifications.models import (
    DeliveryPolicy,
    EnvironmentRelease,
    FeatureProgress,
    LandingStage,
    ReleaseEvidence,
)


class PullRequestRecord(BaseModel):
    repo: str = Field(pattern=r'^[^/\s]+/[^/\s]+$')
    number: int = Field(ge=1)
    merge_sha: str = Field(pattern=r'^[0-9a-f]{40}$')
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    author_login: str = Field(min_length=1)
    coauthor_logins: tuple[str, ...] = ()
    automated: bool = False
    body: str = ''
    changed_files: tuple[str, ...] = ()
    linear_identifier: str | None = None
    linear_url: str | None = None


class FeatureRegistration(BaseModel):
    repo: str = Field(pattern=r'^[^/\s]+/[^/\s]+$')
    pr_number: int = Field(ge=1)
    merge_sha: str = Field(pattern=r'^[0-9a-f]{40}$')
    linear_issue_id: str = Field(min_length=1)
    linear_identifier: str = Field(min_length=1)
    linear_url: str = Field(min_length=1)
    policy: DeliveryPolicy
    progress: FeatureProgress


class PullRequestReference(BaseModel):
    repo: str
    number: int
    title: str
    url: str


class ContributorRelease(BaseModel):
    login: str
    delivery_key: str
    pull_requests: tuple[PullRequestReference, ...]


class FeatureReleaseUpdate(BaseModel):
    linear_issue_id: str
    linear_identifier: str
    linear_url: str
    previous_stage: LandingStage
    current_stage: LandingStage
    became_testable: bool
    became_production_enabled: bool
    evidence: ReleaseEvidence
    progress: FeatureProgress


class ReleasePlan(BaseModel):
    release: EnvironmentRelease
    contributors: tuple[ContributorRelease, ...]
    feature_updates: tuple[FeatureReleaseUpdate, ...]
