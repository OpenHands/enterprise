from server.services.landing_notifications.models import (
    DeliveryPolicy,
    FeatureProgress,
    LandingStage,
)


def derive_stage(policy: DeliveryPolicy, progress: FeatureProgress) -> LandingStage:
    if progress.ga_complete:
        return LandingStage.GA

    ready_environments = frozenset(progress.evidence)
    final_targets_ready = policy.final_targets <= ready_environments
    test_targets_ready = policy.test_targets <= ready_environments

    if progress.council_approved and progress.flag_verified and final_targets_ready:
        return LandingStage.PRODUCTION_ENABLED
    if progress.council_approved:
        return LandingStage.COUNCIL_APPROVED
    if progress.bug_bash_complete:
        return LandingStage.COUNCIL_REVIEW
    if progress.bug_bash_started:
        return LandingStage.BUG_BASH
    if progress.merged and test_targets_ready:
        return LandingStage.TESTABLE
    if progress.merged:
        return LandingStage.MERGED
    return LandingStage.REVIEW
