from copy import deepcopy

import pytest
from sqlalchemy import select

from storage import azure_devops_webhook_diagnostics as diagnostics
from storage.azure_devops_webhook_diagnostics import AzureDevOpsWebhookDiagnostics


@pytest.mark.asyncio
async def test_diagnostics_preserve_attempt_when_observation_changes(
    async_session_maker, monkeypatch
):
    monkeypatch.setattr(diagnostics, 'a_session_maker', async_session_maker)
    attempt = {
        'outcome': 'failed',
        'stage': 'create_work_item_comment',
        'created_subscription_ids': ['pr'],
        'azure_http_status': 400,
    }
    await diagnostics.save_webhook_diagnostics(
        'Contoso', last_install_attempt=deepcopy(attempt)
    )
    await diagnostics.save_webhook_diagnostics(
        'CONTOSO', last_observation={'subscriptions': [], 'delivery_verified': False}
    )
    async with async_session_maker() as session:
        rows = (
            (await session.execute(select(AzureDevOpsWebhookDiagnostics)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].organization == 'contoso'
    assert rows[0].last_install_attempt == attempt
    assert rows[0].last_observation['subscriptions'] == []


@pytest.mark.asyncio
async def test_new_attempt_replaces_only_attempt_and_survives_uninstall(
    async_session_maker, monkeypatch
):
    from storage import azure_devops_webhook_store as installs

    monkeypatch.setattr(diagnostics, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(installs, 'a_session_maker', async_session_maker)
    await diagnostics.save_webhook_diagnostics(
        'contoso',
        last_install_attempt={'outcome': 'failed'},
        last_observation={'subscriptions': ['previous']},
    )
    await diagnostics.save_webhook_diagnostics(
        'contoso',
        last_install_attempt={
            'outcome': 'succeeded',
            'created_subscription_ids': ['new'],
        },
    )
    await diagnostics.save_webhook_diagnostics(
        'fabrikam', last_install_attempt={'outcome': 'failed'}
    )
    await installs.AzureDevOpsWebhookStore().delete_webhook('contoso')
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(AzureDevOpsWebhookDiagnostics).order_by(
                        AzureDevOpsWebhookDiagnostics.organization
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    assert rows[0].last_install_attempt['outcome'] == 'succeeded'
    assert rows[0].last_observation == {'subscriptions': ['previous']}
    assert rows[1].last_install_attempt['outcome'] == 'failed'
