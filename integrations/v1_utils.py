import logging
from typing import Callable, Coroutine
from uuid import UUID

from pydantic import SecretStr

from integrations.utils import CONVERSATION_URL
from openhands.app_server.user_auth.user_auth import UserAuth
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.saas_user_auth import SaasUserAuth
from server.auth.token_manager import TokenManager


def is_budget_exceeded_error(error_message: str) -> bool:
    """Check if an error message indicates a budget exceeded condition.

    This is used to downgrade error logs to info logs for budget exceeded errors
    since they are expected cost control behavior rather than unexpected errors.
    """
    lower_message = error_message.lower()
    return 'budget' in lower_message and 'exceeded' in lower_message


def get_budget_error_message(org_id: UUID, user_id: UUID) -> str:
    """Get the appropriate budget error message based on org type.

    For personal workspaces (org_id == user_id), returns a message about credits.
    For multi-user organizations (org_id != user_id), returns a message about org budget.

    The messages contain specific keywords that the frontend's classifyBudgetOrCreditError()
    function uses to determine which localized error message to show:
    - "OpenHands credits" triggers the credit error UI (STATUS$ERROR_LLM_OUT_OF_CREDITS)
    - "budget" + "exceeded" triggers the org budget error UI (STATUS$ERROR_BUDGET_LIMIT_REACHED)

    Args:
        org_id: The organization ID
        user_id: The user ID

    Returns:
        An error message string with keywords the frontend expects
    """
    is_personal_workspace = org_id == user_id

    if is_personal_workspace:
        # Use "OpenHands credits" keyword for frontend credit error classification
        return "You've run out of OpenHands credits"
    else:
        # Use "budget exceeded" keywords for frontend org budget error classification
        return 'Organization budget limit has been exceeded'


async def handle_callback_error(
    error: Exception,
    conversation_id: UUID,
    service_name: str,
    service_logger: logging.Logger,
    can_post_error: bool,
    post_error_func: Callable[[str], Coroutine],
) -> None:
    """Handle callback processing errors with appropriate logging and user messages.

    This centralizes the error handling logic for V1 callback processors to:
    - Log budget exceeded errors at INFO level (expected cost control behavior)
    - Log other errors at EXCEPTION level
    - Post user-friendly error messages to the integration platform
    - For budget errors, distinguish between personal workspace (credits) and
      multi-user org (budget) errors

    Args:
        error: The exception that occurred
        conversation_id: The conversation ID for logging and linking
        service_name: The service name for log messages (e.g., "GitHub", "GitLab", "Slack")
        service_logger: The logger instance to use for logging
        can_post_error: Whether the prerequisites are met to post an error message
        post_error_func: Async function to post the error message to the platform
    """
    error_str = str(error)
    budget_exceeded = is_budget_exceeded_error(error_str)

    # Log appropriately based on error type
    if budget_exceeded:
        service_logger.info(
            '[%s V1] Budget exceeded for conversation %s: %s',
            service_name,
            conversation_id,
            error,
        )
    else:
        service_logger.exception(
            '[%s V1] Error processing callback: %s', service_name, error
        )

    # Try to post error message to the platform
    if can_post_error:
        try:
            if budget_exceeded:
                # Get org context to determine personal workspace vs multi-user org
                try:
                    from server.utils.conversation_utils import (
                        get_conversation_org_context,
                    )

                    org_id, user_id = await get_conversation_org_context(
                        str(conversation_id)
                    )
                    error_detail = get_budget_error_message(org_id, user_id)
                except Exception as ctx_error:
                    service_logger.warning(
                        '[%s V1] Failed to get org context for budget error, using generic message: %s',
                        service_name,
                        ctx_error,
                    )
                    # Fallback to generic message if we can't determine org type
                    error_detail = 'Budget limit has been exceeded'
            else:
                error_detail = error_str

            await post_error_func(
                f'OpenHands encountered an error: **{error_detail}**\n\n'
                f'[See the conversation]({CONVERSATION_URL.format(conversation_id)}) '
                'for more information.'
            )
        except Exception as post_error:
            service_logger.warning(
                '[%s V1] Failed to post error message to %s: %s',
                service_name,
                service_name,
                post_error,
            )


async def get_saas_user_auth(
    keycloak_user_id: str, token_manager: TokenManager
) -> UserAuth:
    offline_token = await token_manager.load_offline_token(keycloak_user_id)
    if offline_token is None:
        logger.info('no_offline_token_found')

    user_auth = SaasUserAuth(
        user_id=keycloak_user_id,
        refresh_token=SecretStr(offline_token or ''),
    )
    return user_auth
