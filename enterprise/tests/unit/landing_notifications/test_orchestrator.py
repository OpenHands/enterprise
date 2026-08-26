from datetime import UTC, datetime

from server.services.landing_notifications.consumer_models import (
    FeatureRegistration,
    PullRequestRecord,
)
from server.services.landing_notifications.models import (
    DeliveryPolicy,
    Environment,
    EnvironmentRelease,
    FeatureProgress,
    LandingStage,
    ReleaseArtifact,
    ReleaseComponent,
)
from server.services.landing_notifications.notification_models import (
    DeliveryChannel,
    RecipientProfile,
)
from server.services.landing_notifications.orchestrator import plan_release_operations


def test_release_event_plans_tracker_update_and_actionable_delivery() -> None:
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
    pull_request = PullRequestRecord(
        repo='OpenHands/enterprise',
        number=42,
        merge_sha='b' * 40,
        title='feat(orgs): migrate memberships',
        url='https://github.com/OpenHands/enterprise/pull/42',
        author_login='alice',
        body='- Primary E2E test: `frontend/e2e/org-migration.spec.ts`',
        linear_identifier='FEAT-42',
    )
    feature = FeatureRegistration(
        repo='OpenHands/enterprise',
        pr_number=42,
        merge_sha=pull_request.merge_sha,
        linear_issue_id='linear-uuid',
        linear_identifier='FEAT-42',
        linear_url='https://linear.app/openhands/issue/FEAT-42',
        policy=DeliveryPolicy(
            test_targets={Environment.REPLICATED_BETA},
            final_targets={Environment.SAAS_PRODUCTION},
        ),
        progress=FeatureProgress(merged=True),
    )
    recipient = RecipientProfile(
        github_login='alice',
        email='alice@openhands.dev',
        channels={Environment.REPLICATED_BETA: {DeliveryChannel.EMAIL}},
    )

    plan = plan_release_operations(
        release,
        [pull_request],
        [feature],
        [recipient],
    )

    update = plan.release_plan.feature_updates[0]
    assert update.current_stage == LandingStage.TESTABLE
    assert plan.linear_comments[0].issue_id == 'linear-uuid'
    assert plan.deliveries[0].delivery_key.endswith(':alice:email')
    assert 'frontend/e2e/org-migration.spec.ts' in plan.deliveries[0].payload['text']
