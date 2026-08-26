from datetime import UTC, datetime

from server.services.landing_notifications.consumer_models import FeatureReleaseUpdate
from server.services.landing_notifications.linear_updates import plan_linear_comment
from server.services.landing_notifications.models import (
    Environment,
    EnvironmentRelease,
    FeatureProgress,
    LandingStage,
    ReleaseArtifact,
    ReleaseComponent,
    ReleaseEvidence,
)


def test_linear_comment_records_event_and_testable_transition() -> None:
    release = EnvironmentRelease(
        event_id='openhands-cloud:replicated-beta:1450',
        environment=Environment.REPLICATED_BETA,
        released_at=datetime(2026, 8, 26, tzinfo=UTC),
        producer_repo='OpenHands/OpenHands-Cloud',
        producer_sha='a' * 40,
        run_url='https://github.com/OpenHands/OpenHands-Cloud/actions/runs/1',
        environment_url='https://app.beta.example.com',
        artifact=ReleaseArtifact(kind='release', version='1.2.3'),
        components=(
            ReleaseComponent(
                repo='OpenHands/enterprise',
                previous_ref='1.2.2',
                released_ref='1.2.3',
            ),
        ),
    )
    evidence = ReleaseEvidence(
        environment=release.environment,
        event_id=release.event_id,
        artifact_version=release.artifact.version,
        environment_url=release.environment_url,
        run_url=release.run_url,
        released_at=release.released_at,
    )
    update = FeatureReleaseUpdate(
        linear_issue_id='linear-uuid',
        linear_identifier='FEAT-42',
        linear_url='https://linear.app/openhands/issue/FEAT-42',
        previous_stage=LandingStage.MERGED,
        current_stage=LandingStage.TESTABLE,
        became_testable=True,
        became_production_enabled=False,
        evidence=evidence,
        progress=FeatureProgress(
            merged=True,
            evidence={release.environment: evidence},
        ),
    )

    plan = plan_linear_comment(release, update)

    assert plan.issue_id == 'linear-uuid'
    assert (
        '<!-- environment-release:openhands-cloud:replicated-beta:1450 -->' in plan.body
    )
    assert '`merged` → `testable`' in plan.body
    assert 'ready for its planned environment testing' in plan.body
