"""Unit tests for AzureDevOpsWebhookStore."""

from datetime import datetime, timezone

import pytest

from storage.azure_devops_webhook_store import AzureDevOpsWebhookStore


@pytest.fixture
async def webhook_store(async_session_maker):
    import storage.azure_devops_webhook_store as store_module

    store_module.a_session_maker = async_session_maker
    return AzureDevOpsWebhookStore()


@pytest.mark.asyncio
async def test_record_install_stores_installer_time_and_hook_ids(webhook_store):
    before = datetime.now(timezone.utc)

    await webhook_store.record_install(
        'Contoso',
        user_id='kc-owner',
        pr_subscription_id='pr',
        work_item_subscription_id='wi',
    )

    record = await webhook_store.get_webhook('contoso')
    assert record is not None
    assert record.organization == 'contoso'
    assert record.installed_by_user_id == 'kc-owner'
    assert record.installed_at >= before
    assert record.last_verified_at == record.installed_at
    assert (record.pr_subscription_id, record.work_item_subscription_id) == ('pr', 'wi')


@pytest.mark.asyncio
async def test_record_verified_keeps_install_details(webhook_store):
    installed = await webhook_store.record_install(
        'contoso',
        user_id='kc-owner',
        pr_subscription_id='pr',
        work_item_subscription_id='wi',
    )

    await webhook_store.record_verified(
        'CONTOSO', pr_subscription_id='pr2', work_item_subscription_id='wi2'
    )

    record = await webhook_store.get_webhook('Contoso')
    assert record.id == installed.id
    assert record.installed_by_user_id == 'kc-owner'
    assert record.installed_at == installed.installed_at
    assert record.last_verified_at > installed.installed_at
    assert record.pr_subscription_id == 'pr2'


@pytest.mark.asyncio
async def test_record_verified_creates_a_record_without_install_details(webhook_store):
    await webhook_store.record_verified(
        'contoso', pr_subscription_id='pr', work_item_subscription_id='wi'
    )

    record = await webhook_store.get_webhook('contoso')
    assert record.installed_at is None
    assert record.installed_by_user_id is None
    assert record.last_verified_at is not None


@pytest.mark.asyncio
async def test_delete_webhook_removes_only_that_organization(webhook_store):
    for org in ('contoso', 'fabrikam'):
        await webhook_store.record_install(
            org,
            user_id='kc-owner',
            pr_subscription_id='pr',
            work_item_subscription_id='wi',
        )

    assert await webhook_store.delete_webhook('Contoso') is True
    assert await webhook_store.delete_webhook('contoso') is False
    assert await webhook_store.get_webhook('contoso') is None
    assert await webhook_store.get_webhook('fabrikam') is not None
