from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from server.services.landing_notifications import (
    DeliveryPolicy,
    Environment,
    EnvironmentRelease,
    FeatureProgress,
    LandingStage,
    ReleaseArtifact,
    ReleaseComponent,
    ReleaseEvidence,
    derive_stage,
)

POLICY = DeliveryPolicy(
    test_targets={Environment.SAAS_STAGING, Environment.REPLICATED_BETA},
    final_targets={Environment.SAAS_PRODUCTION, Environment.REPLICATED_STABLE},
)


def evidence(environment: Environment) -> ReleaseEvidence:
    return ReleaseEvidence(
        environment=environment,
        event_id=f'event:{environment}',
        artifact_version='1.2.3',
        environment_url=f'https://{environment}.example.com',
        run_url='https://github.com/OpenHands/enterprise/actions/runs/1',
        released_at=datetime(2026, 8, 26, tzinfo=UTC),
    )


def test_stage_waits_for_every_required_test_target() -> None:
    progress = FeatureProgress(
        merged=True,
        evidence={Environment.SAAS_STAGING: evidence(Environment.SAAS_STAGING)},
    )

    assert derive_stage(POLICY, progress) == LandingStage.MERGED

    progress.evidence[Environment.REPLICATED_BETA] = evidence(
        Environment.REPLICATED_BETA
    )
    assert derive_stage(POLICY, progress) == LandingStage.TESTABLE


def test_parallel_delivery_tracks_do_not_imply_each_other() -> None:
    progress = FeatureProgress(
        merged=True,
        evidence={
            Environment.REPLICATED_UNSTABLE: evidence(Environment.REPLICATED_UNSTABLE),
            Environment.REPLICATED_BETA: evidence(Environment.REPLICATED_BETA),
            Environment.REPLICATED_STABLE: evidence(Environment.REPLICATED_STABLE),
        },
    )

    assert derive_stage(POLICY, progress) == LandingStage.MERGED


def test_production_enabled_requires_approval_flag_and_final_targets() -> None:
    progress = FeatureProgress(
        merged=True,
        bug_bash_started=True,
        bug_bash_complete=True,
        council_approved=True,
        flag_verified=True,
        evidence={
            Environment.SAAS_STAGING: evidence(Environment.SAAS_STAGING),
            Environment.REPLICATED_BETA: evidence(Environment.REPLICATED_BETA),
            Environment.SAAS_PRODUCTION: evidence(Environment.SAAS_PRODUCTION),
        },
    )

    assert derive_stage(POLICY, progress) == LandingStage.COUNCIL_APPROVED

    progress.evidence[Environment.REPLICATED_STABLE] = evidence(
        Environment.REPLICATED_STABLE
    )
    assert derive_stage(POLICY, progress) == LandingStage.PRODUCTION_ENABLED


def test_release_event_rejects_duplicate_component_repositories() -> None:
    component = ReleaseComponent(
        repo='OpenHands/enterprise', previous_ref='1.2.2', released_ref='1.2.3'
    )

    with pytest.raises(ValidationError, match='each repository at most once'):
        EnvironmentRelease(
            event_id='openhands-cloud:replicated-beta:1450',
            environment=Environment.REPLICATED_BETA,
            released_at=datetime(2026, 8, 26, tzinfo=UTC),
            producer_repo='OpenHands/OpenHands-Cloud',
            producer_sha='a' * 40,
            run_url='https://github.com/OpenHands/OpenHands-Cloud/actions/runs/1',
            environment_url='https://app.beta.example.com',
            artifact=ReleaseArtifact(
                kind='replicated-release', version='1.2.3', sequence=1450
            ),
            components=(component, component),
        )


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReleaseEvidence(
            environment='qa',
            event_id='event:qa',
            artifact_version='1.2.3',
            environment_url='https://qa.example.com',
            run_url='https://github.com/OpenHands/enterprise/actions/runs/1',
            released_at=datetime(2026, 8, 26, tzinfo=UTC),
        )
