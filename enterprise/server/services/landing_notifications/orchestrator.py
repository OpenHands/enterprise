from pydantic import BaseModel
from server.services.landing_notifications.consumer import plan_release
from server.services.landing_notifications.consumer_models import (
    FeatureRegistration,
    PullRequestRecord,
    ReleasePlan,
)
from server.services.landing_notifications.guidance_models import LinearIssueContext
from server.services.landing_notifications.linear_updates import (
    LinearCommentPlan,
    plan_linear_comment,
)
from server.services.landing_notifications.models import EnvironmentRelease
from server.services.landing_notifications.notification_models import (
    DeliveryAttempt,
    RecipientProfile,
)
from server.services.landing_notifications.notifications import (
    plan_delivery_attempts,
    render_notification,
)


class ReleaseOperationPlan(BaseModel):
    release_plan: ReleasePlan
    linear_comments: tuple[LinearCommentPlan, ...]
    deliveries: tuple[DeliveryAttempt, ...]


def plan_release_operations(
    release: EnvironmentRelease,
    pull_requests: list[PullRequestRecord],
    features: list[FeatureRegistration],
    recipients: list[RecipientProfile],
    *,
    linear_issues: dict[str, LinearIssueContext] | None = None,
    delivered_keys: set[str] | None = None,
) -> ReleaseOperationPlan:
    release_plan = plan_release(release, pull_requests, features)
    profiles = {profile.github_login: profile for profile in recipients}
    deliveries: list[DeliveryAttempt] = []
    for contributor in release_plan.contributors:
        profile = profiles.get(contributor.login)
        if not profile:
            continue
        content = render_notification(
            release,
            contributor,
            pull_requests,
            linear_issues,
        )
        deliveries.extend(
            plan_delivery_attempts(
                release,
                profile,
                content,
                delivered_keys or set(),
            )
        )

    return ReleaseOperationPlan(
        release_plan=release_plan,
        linear_comments=tuple(
            plan_linear_comment(release, update)
            for update in release_plan.feature_updates
        ),
        deliveries=tuple(deliveries),
    )
