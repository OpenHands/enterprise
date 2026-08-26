from collections import defaultdict
from collections.abc import Iterable

from server.services.landing_notifications.consumer_models import (
    ContributorRelease,
    FeatureRegistration,
    FeatureReleaseUpdate,
    PullRequestRecord,
    PullRequestReference,
    ReleasePlan,
)
from server.services.landing_notifications.models import (
    EnvironmentRelease,
    LandingStage,
    ReleaseEvidence,
)
from server.services.landing_notifications.policy import derive_stage


def plan_release(
    release: EnvironmentRelease,
    pull_requests: list[PullRequestRecord],
    features: list[FeatureRegistration],
) -> ReleasePlan:
    component_repos = {component.repo for component in release.components}
    included = _deduplicate_pull_requests(
        pull_request
        for pull_request in pull_requests
        if pull_request.repo in component_repos
    )
    included_keys = {
        (pull_request.repo, pull_request.number) for pull_request in included
    }

    return ReleasePlan(
        release=release,
        contributors=_plan_contributors(release.event_id, included),
        feature_updates=_plan_feature_updates(release, included_keys, features),
    )


def _deduplicate_pull_requests(
    pull_requests: Iterable[PullRequestRecord],
) -> tuple[PullRequestRecord, ...]:
    by_key: dict[tuple[str, int], PullRequestRecord] = {}
    for pull_request in pull_requests:
        by_key[(pull_request.repo, pull_request.number)] = pull_request
    return tuple(by_key[key] for key in sorted(by_key))


def _plan_contributors(
    event_id: str, pull_requests: tuple[PullRequestRecord, ...]
) -> tuple[ContributorRelease, ...]:
    by_login: defaultdict[str, list[PullRequestReference]] = defaultdict(list)
    for pull_request in pull_requests:
        if pull_request.automated:
            continue
        reference = PullRequestReference(
            repo=pull_request.repo,
            number=pull_request.number,
            title=pull_request.title,
            url=pull_request.url,
        )
        logins = {pull_request.author_login, *pull_request.coauthor_logins}
        for login in logins:
            if _is_bot(login):
                continue
            by_login[login].append(reference)

    return tuple(
        ContributorRelease(
            login=login,
            delivery_key=f'{event_id}:{login}',
            pull_requests=tuple(
                sorted(by_login[login], key=lambda value: (value.repo, value.number))
            ),
        )
        for login in sorted(by_login)
    )


def _plan_feature_updates(
    release: EnvironmentRelease,
    included_keys: set[tuple[str, int]],
    features: list[FeatureRegistration],
) -> tuple[FeatureReleaseUpdate, ...]:
    updates: list[FeatureReleaseUpdate] = []
    for feature in features:
        if (feature.repo, feature.pr_number) not in included_keys:
            continue

        existing = feature.progress.evidence.get(release.environment)
        if existing and existing.event_id == release.event_id:
            continue

        previous_stage = derive_stage(feature.policy, feature.progress)
        progress = feature.progress.model_copy(deep=True)
        evidence = ReleaseEvidence(
            environment=release.environment,
            event_id=release.event_id,
            artifact_version=release.artifact.version,
            environment_url=release.environment_url,
            run_url=release.run_url,
            released_at=release.released_at,
        )
        progress.evidence[release.environment] = evidence
        current_stage = derive_stage(feature.policy, progress)
        updates.append(
            FeatureReleaseUpdate(
                linear_issue_id=feature.linear_issue_id,
                linear_identifier=feature.linear_identifier,
                linear_url=feature.linear_url,
                previous_stage=previous_stage,
                current_stage=current_stage,
                became_testable=(
                    previous_stage != LandingStage.TESTABLE
                    and current_stage == LandingStage.TESTABLE
                ),
                became_production_enabled=(
                    previous_stage != LandingStage.PRODUCTION_ENABLED
                    and current_stage == LandingStage.PRODUCTION_ENABLED
                ),
                evidence=evidence,
                progress=progress,
            )
        )
    return tuple(sorted(updates, key=lambda update: update.linear_identifier))


def _is_bot(login: str) -> bool:
    normalized = login.casefold()
    return normalized.endswith('[bot]') or normalized.endswith('-bot')
