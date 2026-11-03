"""The instance ID: created once, shared by every replica, never changed."""

import asyncio
from unittest.mock import patch

import pytest

from storage.instance_settings import InstanceSettings
from storage.instance_settings_store import InstanceSettingsStore


@pytest.fixture
def store_db(async_session_maker):
    with patch('storage.instance_settings_store.a_session_maker', async_session_maker):
        yield async_session_maker


async def test_creates_the_row_and_the_id_when_neither_exists(store_db):
    instance_id = await InstanceSettingsStore.get_or_create_instance_id()

    async with store_db() as session:
        settings = await session.get(InstanceSettings, 1)
    assert settings is not None
    assert settings.instance_id == instance_id


async def test_returns_the_same_id_every_time(store_db):
    first = await InstanceSettingsStore.get_or_create_instance_id()
    second = await InstanceSettingsStore.get_or_create_instance_id()

    assert first == second


async def test_adds_the_id_to_an_existing_row_without_touching_it(store_db):
    async with store_db() as session:
        session.add(InstanceSettings(id=1, company_name='Example Co'))
        await session.commit()

    instance_id = await InstanceSettingsStore.get_or_create_instance_id()

    async with store_db() as session:
        settings = await session.get(InstanceSettings, 1)
    assert settings is not None
    assert settings.instance_id == instance_id
    assert settings.company_name == 'Example Co'


async def test_racing_replicas_all_get_the_first_id(store_db):
    ids = await asyncio.gather(
        *(InstanceSettingsStore.get_or_create_instance_id() for _ in range(8))
    )

    assert len(set(ids)) == 1
    async with store_db() as session:
        settings = await session.get(InstanceSettings, 1)
    assert settings is not None
    assert settings.instance_id == ids[0]
