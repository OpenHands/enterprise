"""HTTP capability boundary for features backed by the LLM gateway."""

from fastapi import HTTPException, status

from openhands.app_server.utils.litellm_integration import is_litellm_enabled


def require_litellm_available() -> None:
    """Reject unavailable gateway features before reads or mutations."""
    if not is_litellm_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                'code': 'litellm_disabled',
                'message': 'This feature requires the LLM gateway, which is disabled.',
            },
        )
