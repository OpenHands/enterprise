from server.services.landing_notifications.consumer_models import PullRequestRecord
from server.services.landing_notifications.guidance import build_test_guidance
from server.services.landing_notifications.guidance_models import (
    GuidanceKind,
    GuidanceSource,
    LinearIssueContext,
)


def pull_request(**overrides: object) -> PullRequestRecord:
    values: dict[str, object] = {
        'repo': 'OpenHands/enterprise',
        'number': 42,
        'merge_sha': 'a' * 40,
        'title': 'feat(orgs): migrate memberships',
        'url': 'https://github.com/OpenHands/enterprise/pull/42',
        'author_login': 'alice',
    }
    values.update(overrides)
    return PullRequestRecord.model_validate(values)


def test_links_declared_e2e_test_at_released_commit() -> None:
    pr = pull_request(
        body='- Primary E2E test: `tests/e2e/test_orgs.py::test_migrate_members`'
    )

    guidance = build_test_guidance(pr)

    instruction = guidance.instructions[0]
    assert instruction.kind == GuidanceKind.VERIFIED_E2E
    assert instruction.source == GuidanceSource.PRIMARY_E2E
    assert instruction.text.endswith('test_migrate_members')
    assert instruction.url == (
        'https://github.com/OpenHands/enterprise/blob/'
        f'{pr.merge_sha}/tests/e2e/test_orgs.py'
    )


def test_discovers_changed_e2e_files_but_not_unit_tests() -> None:
    pr = pull_request(
        changed_files=(
            'frontend/e2e/org-migration.spec.ts',
            'enterprise/tests/unit/test_org_migration.py',
        )
    )

    guidance = build_test_guidance(pr)

    assert len(guidance.instructions) == 1
    assert guidance.instructions[0].source == GuidanceSource.CHANGED_E2E
    assert guidance.instructions[0].url.endswith('/frontend/e2e/org-migration.spec.ts')


def test_uses_pr_how_to_test_as_labelled_suggestions() -> None:
    pr = pull_request(
        body="""## How to Test

1. Create a test organization.
2. Run the membership migration.
3. Verify every member retains their role.

## Notes
Nothing else.
"""
    )

    guidance = build_test_guidance(pr)

    assert [item.kind for item in guidance.instructions] == [
        GuidanceKind.SUGGESTED,
        GuidanceKind.SUGGESTED,
        GuidanceKind.SUGGESTED,
    ]
    assert all(
        item.source == GuidanceSource.PR_HOW_TO_TEST for item in guidance.instructions
    )
    assert guidance.instructions[0].text == 'Suggested: Create a test organization.'


def test_uses_linear_acceptance_criteria_with_provenance() -> None:
    pr = pull_request(body='No test section')
    issue = LinearIssueContext(
        identifier='FEAT-42',
        url='https://linear.app/openhands/issue/FEAT-42',
        title='Organization migration',
        description="""## Acceptance Criteria
- Existing owners remain owners.
- Suspended users remain suspended.
""",
    )

    guidance = build_test_guidance(pr, issue)

    assert len(guidance.instructions) == 2
    assert all(
        item.source == GuidanceSource.LINEAR_ACCEPTANCE_CRITERIA
        for item in guidance.instructions
    )
    assert all(item.url == issue.url for item in guidance.instructions)


def test_preserves_explicit_e2e_url() -> None:
    url = 'https://github.com/OpenHands/enterprise/blob/main/frontend/e2e/orgs.spec.ts'
    pr = pull_request(body=f'- Primary E2E test: `{url}`')

    guidance = build_test_guidance(pr)

    assert guidance.instructions[0].url == url
    assert guidance.has_verified_e2e


def test_fallback_is_a_suggestion_not_a_verified_test() -> None:
    guidance = build_test_guidance(pull_request())

    assert guidance.instructions[0].kind == GuidanceKind.SUGGESTED
    assert guidance.instructions[0].source == GuidanceSource.PR_SUMMARY
    assert not guidance.has_verified_e2e
