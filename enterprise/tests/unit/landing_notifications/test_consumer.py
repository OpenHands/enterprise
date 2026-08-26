from datetime import UTC, datetime

from server.services.landing_notifications.consumer import plan_release
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
    ReleaseEvidence,
)


def release(
    environment: Environment = Environment.REPLICATED_BETA,
) -> EnvironmentRelease:
    return EnvironmentRelease(
        event_id=f'openhands-cloud:{environment}:1450',
        environment=environment,
        released_at=datetime(2026, 8, 26, tzinfo=UTC),
        producer_repo='OpenHands/OpenHands-Cloud',
        producer_sha='a' * 40,
        run_url='https://github.com/OpenHands/OpenHands-Cloud/actions/runs/1',
        environment_url=f'https://app.{environment}.example.com',
        artifact=ReleaseArtifact(
            kind='replicated-release', version='1.2.3', sequence=1450
        ),
        components=(
            ReleaseComponent(
                repo='OpenHands/enterprise',
                previous_ref='1.2.2',
                released_ref='1.2.3',
            ),
        ),
    )


def pull_request(
    number: int = 42,
    author: str = 'alice',
    **overrides: object,
) -> PullRequestRecord:
    values: dict[str, object] = {
        'repo': 'OpenHands/enterprise',
        'number': number,
        'merge_sha': f'{number:040x}',
        'title': 'feat(orgs): migrate memberships',
        'url': f'https://github.com/OpenHands/enterprise/pull/{number}',
        'author_login': author,
        'coauthor_logins': (),
    }
    values.update(overrides)
    return PullRequestRecord.model_validate(values)


def feature(progress: FeatureProgress | None = None) -> FeatureRegistration:
    return FeatureRegistration(
        repo='OpenHands/enterprise',
        pr_number=42,
        merge_sha=f'{42:040x}',
        linear_issue_id='linear-uuid',
        linear_identifier='FEAT-42',
        linear_url='https://linear.app/openhands/issue/FEAT-42',
        policy=DeliveryPolicy(
            test_targets={Environment.SAAS_STAGING, Environment.REPLICATED_BETA},
            final_targets={Environment.SAAS_PRODUCTION},
        ),
        progress=progress or FeatureProgress(merged=True),
    )


def test_groups_authors_and_coauthors_without_duplicates() -> None:
    plan = plan_release(
        release(),
        [
            pull_request(coauthor_logins=('bob', 'alice')),
            pull_request(number=43, author='bob'),
        ],
        [],
    )

    assert [contributor.login for contributor in plan.contributors] == ['alice', 'bob']
    assert [reference.number for reference in plan.contributors[0].pull_requests] == [
        42
    ]
    assert [reference.number for reference in plan.contributors[1].pull_requests] == [
        42,
        43,
    ]


def test_excludes_automated_changes_and_bot_accounts() -> None:
    plan = plan_release(
        release(),
        [
            pull_request(author='release-please[bot]'),
            pull_request(number=43, automated=True),
        ],
        [],
    )

    assert plan.contributors == ()


def test_environment_event_advances_feature_when_test_policy_is_satisfied() -> None:
    staging = ReleaseEvidence(
        environment=Environment.SAAS_STAGING,
        event_id='saas:staging:1',
        artifact_version='1.2.3-rc.1',
        environment_url='https://staging.example.com',
        run_url='https://github.com/OpenHands/saas-deploy/actions/runs/1',
        released_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    registration = feature(
        FeatureProgress(
            merged=True,
            evidence={Environment.SAAS_STAGING: staging},
        )
    )

    plan = plan_release(release(), [pull_request()], [registration])

    update = plan.feature_updates[0]
    assert update.previous_stage == LandingStage.MERGED
    assert update.current_stage == LandingStage.TESTABLE
    assert update.became_testable
    assert (
        update.progress.evidence[Environment.REPLICATED_BETA].event_id
        == release().event_id
    )


def test_same_event_is_idempotent_for_feature_updates() -> None:
    current_release = release()
    beta = ReleaseEvidence(
        environment=Environment.REPLICATED_BETA,
        event_id=current_release.event_id,
        artifact_version='1.2.3',
        environment_url=current_release.environment_url,
        run_url=current_release.run_url,
        released_at=current_release.released_at,
    )
    registration = feature(
        FeatureProgress(
            merged=True,
            evidence={Environment.REPLICATED_BETA: beta},
        )
    )

    plan = plan_release(current_release, [pull_request()], [registration])

    assert plan.feature_updates == ()


def test_ignores_pull_requests_outside_release_components() -> None:
    unrelated = pull_request(repo='OpenHands/docs')

    plan = plan_release(release(), [unrelated], [])

    assert plan.contributors == ()
