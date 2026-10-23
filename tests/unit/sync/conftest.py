"""Fixtures for the GitLab webhook installer's real-database tests."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, update

import storage.gitlab_webhook_store as store_module
import sync.install_gitlab_webhooks as job_module
from storage.gitlab_hook_intent import GitlabHookIntent
from storage.gitlab_webhook import GitlabWebhook
from storage.gitlab_webhook_store import GitlabWebhookStore
from sync.install_gitlab_webhooks import CLAIM_LEASE
from tests.unit.gitlab_fakes import USER, FakeGitLab


@pytest.fixture
def gitlab():
    return FakeGitLab()


@pytest.fixture
def store(async_session_maker, gitlab, monkeypatch):
    monkeypatch.setattr(store_module, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(job_module, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(job_module, 'GitLabServiceImpl', gitlab.for_user)
    return GitlabWebhookStore()


@pytest.fixture
def add_row(async_session_maker):
    async def add(project_id='project-1', **fields):
        fields.setdefault('webhook_exists', False)
        row = GitlabWebhook(user_id=USER, project_id=project_id, **fields)
        async with async_session_maker() as session, session.begin():
            session.add(row)
        return row.id

    return add


@pytest.fixture
def add_intent(async_session_maker):
    async def add(row_id, hook_uuid, state, run_id=None):
        async with async_session_maker() as session, session.begin():
            session.add(
                GitlabHookIntent(
                    uuid=hook_uuid, webhook_id=row_id, state=state, run_id=run_id
                )
            )

    return add


@pytest.fixture
def get_row(async_session_maker):
    async def get(row_id):
        async with async_session_maker() as session:
            return await session.get(GitlabWebhook, row_id)

    return get


@pytest.fixture
def take_over(async_session_maker, store):
    """Age a row's claim past the lease and claim it for a new run."""

    async def steal(row_id):
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(GitlabWebhook)
                .where(GitlabWebhook.id == row_id)
                .values(claimed_at=func.now() - CLAIM_LEASE - timedelta(minutes=1))
            )
        run_b = uuid4()
        claimed = await store.claim_rows(run_b, CLAIM_LEASE)
        assert [row.id for row in claimed] == [row_id]
        return run_b

    return steal
