"""save_full_pr's write guard, against a real database.

Enrichment is first-successful-completion-wins. A run whose stats write is
refused must not overwrite the winner's saved PR file.
"""

import json
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from integrations.github.data_collector import GitHubDataCollector
from integrations.types import PRStatus
from openhands.app_server.integrations.service_types import ProviderType
from storage.database import a_session_maker
from storage.openhands_pr import OpenhandsPR
from storage.openhands_pr_store import OpenhandsPRStore

REPO_ID = '123'
PR_NUMBER = 7
MAX_RETRIES = 3

_GRAPHQL_RESULT = {
    'data': {
        'node': {
            'name': 'repo',
            'owner': {'login': 'org'},
            'languages': {'nodes': []},
            'pullRequest': {
                'number': PR_NUMBER,
                'title': 'title',
                'body': 'body',
                'author': {'login': 'alice'},
                'merged': False,
                'state': 'CLOSED',
                'commits': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
                'comments': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
                'reviews': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
            },
        }
    }
}

pytestmark = pytest.mark.usefixtures('app_db_session')


class _RecordingFileStore:
    def __init__(self):
        self.writes: list[tuple[str, str]] = []

    def write(self, path: str, data: str) -> None:
        self.writes.append((path, data))


@contextmanager
def _github_stubbed():
    """Replace only the external GitHub calls save_full_pr makes."""
    gh_client = MagicMock()
    gh_client.execute_graphql_query = AsyncMock(return_value=_GRAPHQL_RESULT)
    with (
        patch.object(
            GitHubDataCollector,
            '_get_installation_access_token',
            return_value='token',
        ),
        patch.object(
            GitHubDataCollector,
            '_get_repo_node_id',
            new=AsyncMock(return_value='R_x'),
        ),
        patch(
            'integrations.github.data_collector.GithubServiceImpl',
            return_value=gh_client,
        ),
    ):
        yield


def _collector(file_store: _RecordingFileStore) -> GitHubDataCollector:
    # __new__ skips the constructor, which builds a GitHub App client.
    collector = GitHubDataCollector.__new__(GitHubDataCollector)
    collector.file_store = file_store
    collector.full_saved_pr_path = 'prs/github/{}-{}/data.json'
    return collector


async def _seed_claimed() -> OpenhandsPR:
    store = OpenhandsPRStore()
    await store.insert_pr(
        OpenhandsPR(
            repo_id=REPO_ID,
            repo_name='org/repo',
            pr_number=PR_NUMBER,
            status=PRStatus.CLOSED,
            provider=ProviderType.GITHUB.value,
            installation_id='42',
            closed_at=datetime(2026, 1, 2),
            created_at=datetime(2026, 1, 1),
        )
    )
    claimed = await store.claim_pr_for_processing(REPO_ID, PR_NUMBER, MAX_RETRIES)
    assert claimed is not None
    return claimed


async def _load() -> OpenhandsPR:
    async with a_session_maker() as session:
        result = await session.execute(
            select(OpenhandsPR).filter(
                OpenhandsPR.repo_id == REPO_ID, OpenhandsPR.pr_number == PR_NUMBER
            )
        )
        return result.scalars().one()


async def test_saves_the_file_when_the_stats_are_recorded():
    claimed = await _seed_claimed()
    file_store = _RecordingFileStore()

    with _github_stubbed():
        await _collector(file_store).save_full_pr(claimed)

    assert len(file_store.writes) == 1
    path, payload = file_store.writes[0]
    assert path == f'prs/github/{REPO_ID}-{PR_NUMBER}/data.json'
    assert json.loads(payload)['pr_metadata']['number'] == PR_NUMBER
    assert (await _load()).processed is True


async def test_keeps_the_winners_file_when_the_stats_write_is_refused():
    """An overlapping run that finishes second must not replace the saved file."""
    claimed = await _seed_claimed()
    store = OpenhandsPRStore()
    assert (
        await store.update_pr_openhands_stats(
            repo_id=REPO_ID,
            pr_number=PR_NUMBER,
            original_updated_at=claimed.updated_at,
            openhands_helped_author=True,
            num_openhands_commits=5,
            num_openhands_review_comments=6,
            num_openhands_general_comments=7,
        )
        is True
    )

    file_store = _RecordingFileStore()
    with _github_stubbed():
        await _collector(file_store).save_full_pr(claimed)

    assert file_store.writes == []
    assert (await _load()).num_openhands_commits == 5
