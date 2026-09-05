"""Tests for the Azure DevOps bot posting-identity credential."""

from unittest.mock import patch

from integrations.azure_devops.azure_devops_bot_credential import (
    AdoPatBotCredential,
    get_azure_devops_bot_credential,
)


def test_get_credential_none_when_token_unset():
    """No bot token configured -> post as the mentioner (returns None)."""
    with patch(
        'integrations.azure_devops.azure_devops_bot_credential.AZURE_DEVOPS_BOT_TOKEN',
        '',
    ):
        assert get_azure_devops_bot_credential() is None


def test_get_credential_returns_pat_credential_when_token_set():
    """A bot token yields a PAT credential."""
    with patch(
        'integrations.azure_devops.azure_devops_bot_credential.AZURE_DEVOPS_BOT_TOKEN',
        'bot-pat-123',
    ):
        credential = get_azure_devops_bot_credential()
    assert isinstance(credential, AdoPatBotCredential)


def test_pat_credential_builds_service_with_raw_token_and_no_refresh():
    """build_service sets the raw PAT (Basic auth) and disables refresh."""
    with patch(
        'integrations.azure_devops.azure_devops_bot_credential.SaaSAzureDevOpsService'
    ) as service_cls:
        service = AdoPatBotCredential('bot-pat-123').build_service()

    # No per-user auth; raw PAT set directly so _get_headers uses Basic ':<pat>'.
    service_cls.assert_called_once_with()
    assert 'external_auth_id' not in service_cls.call_args.kwargs
    assert service.token.get_secret_value() == 'bot-pat-123'
    assert service.refresh is False
