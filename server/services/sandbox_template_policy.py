"""Helm-managed organization template access and organization-wide rollout."""

import logging
import os
from uuid import UUID

from openhands.app_server.sandbox.sandbox_template_policy import (
    ORG_TEMPLATES_ENV as ORG_TEMPLATES_ENV,
)
from openhands.app_server.sandbox.sandbox_template_policy import (
    SandboxTemplatePolicy,
)
from openhands.app_server.sandbox.sandbox_template_policy import (
    parse_org_template_config as _parse_org_templates,
)
from server.services.feature_flag_service import feature_flag_service

SANDBOX_SELECTOR_FLAG = 'ENABLE_SANDBOX_SELECTOR'
_logger = logging.getLogger(__name__)
feature_flag_service.register_env_default(
    SANDBOX_SELECTOR_FLAG, SANDBOX_SELECTOR_FLAG, False
)


async def get_org_template_policy(org_id: UUID | None) -> SandboxTemplatePolicy | None:
    if org_id is None:
        return None
    policies = _parse_org_templates(os.environ.get(ORG_TEMPLATES_ENV, '{}'))
    policy = policies.get(org_id)
    if policy is None:
        return None
    # Deliberately omit user/email context: all members see the same org flag.
    try:
        enabled = await feature_flag_service.is_enabled(
            SANDBOX_SELECTOR_FLAG, org_id=str(org_id)
        )
    except Exception:
        # A rollout flag outage must not break ordinary conversation routes.
        # Fail closed for the selector while preserving the org's allowlist.
        _logger.exception('Sandbox selector flag evaluation failed; disabling selector')
        enabled = False
    return policy.model_copy(update={'selector_enabled': enabled})
