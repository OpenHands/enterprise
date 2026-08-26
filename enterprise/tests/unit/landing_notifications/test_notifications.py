from datetime import UTC, datetime

from server.services.landing_notifications.consumer_models import (
    ContributorRelease,
    PullRequestRecord,
    PullRequestReference,
)
from server.services.landing_notifications.guidance_models import LinearIssueContext
from server.services.landing_notifications.models import (
    Environment,
    EnvironmentRelease,
    ReleaseArtifact,
    ReleaseComponent,
)
from server.services.landing_notifications.notification_models import (
    DeliveryChannel,
    RecipientProfile,
)
from server.services.landing_notifications.notifications import (
    plan_delivery_attempts,
    render_notification,
)


def release(environment: Environment) -> EnvironmentRelease:
    return EnvironmentRelease(
        event_id=f'release:{environment}:1',
        environment=environment,
        released_at=datetime(2026, 8, 26, tzinfo=UTC),
        producer_repo='OpenHands/OpenHands-Cloud',
        producer_sha='a' * 40,
        run_url='https://github.com/OpenHands/OpenHands-Cloud/actions/runs/1',
        environment_url=f'https://app.{environment}.example.com',
        artifact=ReleaseArtifact(kind='chart', version='1.2.3'),
        components=(
            ReleaseComponent(
                repo='OpenHands/enterprise',
                previous_ref='1.2.2',
                released_ref='1.2.3',
            ),
        ),
    )


def pull_request() -> PullRequestRecord:
    return PullRequestRecord(
        repo='OpenHands/enterprise',
        number=42,
        merge_sha='b' * 40,
        title='feat(orgs): migrate <members>',
        url='https://github.com/OpenHands/enterprise/pull/42',
        author_login='alice',
        body='- Primary E2E test: `frontend/e2e/org-migration.spec.ts`',
        linear_identifier='FEAT-42',
        linear_url='https://linear.app/openhands/issue/FEAT-42',
    )


def contributor() -> ContributorRelease:
    return ContributorRelease(
        login='alice',
        delivery_key='unused',
        pull_requests=(
            PullRequestReference(
                repo='OpenHands/enterprise',
                number=42,
                title='feat(orgs): migrate members',
                url='https://github.com/OpenHands/enterprise/pull/42',
            ),
        ),
    )


def test_test_environment_notification_links_verified_e2e() -> None:
    current_release = release(Environment.REPLICATED_BETA)

    content = render_notification(current_release, contributor(), [pull_request()])

    assert content.subject == 'Your changes are ready to test in Replicated beta'
    assert '[Verified E2E]' in content.text
    assert current_release.environment_url in content.text
    assert '/frontend/e2e/org-migration.spec.ts' in content.text
    assert '&lt;members&gt;' in content.html
    assert '<members>' not in content.html
    slack_text = content.slack_blocks[2]['text']['text']
    assert '&lt;members&gt;' in slack_text
    assert '<members>' not in slack_text
    assert '<https://github.com/' in slack_text


def test_production_notification_still_includes_test_action() -> None:
    current_release = release(Environment.SAAS_PRODUCTION)
    pr = pull_request().model_copy(
        update={
            'body': '## How to Test\n- Create an organization.\n- Verify memberships.'
        }
    )
    issue = LinearIssueContext(
        identifier='FEAT-42',
        url='https://linear.app/openhands/issue/FEAT-42',
        title='Organization migration',
        description='## Acceptance Criteria\n- Owners retain access.',
    )

    content = render_notification(
        current_release,
        contributor(),
        [pr],
        {'FEAT-42': issue},
    )

    assert content.subject == 'Your changes are live in SaaS production'
    assert '[Suggested check] Suggested: Create an organization.' in content.text
    assert issue.url in content.text


def test_delivery_plan_respects_preferences_and_idempotency() -> None:
    current_release = release(Environment.REPLICATED_BETA)
    content = render_notification(current_release, contributor(), [pull_request()])
    profile = RecipientProfile(
        github_login='alice',
        email='alice@openhands.dev',
        slack_user_id='U123',
        channels={
            Environment.REPLICATED_BETA: {
                DeliveryChannel.EMAIL,
                DeliveryChannel.SLACK,
            }
        },
    )
    delivered = {f'{current_release.event_id}:alice:email'}

    attempts = plan_delivery_attempts(current_release, profile, content, delivered)

    assert len(attempts) == 1
    assert attempts[0].channel == DeliveryChannel.SLACK
    assert attempts[0].delivery_key.endswith(':alice:slack')
    assert attempts[0].payload['channel'] == 'U123'


def test_delivery_plan_skips_channels_without_an_address() -> None:
    current_release = release(Environment.SAAS_STAGING)
    content = render_notification(current_release, contributor(), [pull_request()])
    profile = RecipientProfile(
        github_login='alice',
        channels={Environment.SAAS_STAGING: {DeliveryChannel.EMAIL}},
    )

    assert plan_delivery_attempts(current_release, profile, content, set()) == ()
