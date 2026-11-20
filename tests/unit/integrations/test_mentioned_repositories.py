from unittest.mock import AsyncMock, MagicMock

import pytest

from integrations.mentioned_repositories import (
    MentionedRepositories,
    join_names,
    select_additional_repositories,
    verify_mentioned_repos,
)
from openhands.app_server.app_conversation.additional_repositories import (
    MAX_ADDITIONAL_REPOSITORIES,
)
from openhands.app_server.integrations.service_types import ProviderType, Repository


def _repo(name: str) -> Repository:
    return Repository(
        id=name, full_name=name, git_provider=ProviderType.GITHUB, is_public=False
    )


def _handler(accessible: list[str]):
    by_name = {n.lower(): _repo(n) for n in accessible}

    async def _verify(name, *args, **kwargs):
        if name.lower() not in by_name:
            raise Exception(f'404 {name}')
        return by_name[name.lower()]

    handler = MagicMock()
    handler.verify_repo_provider = AsyncMock(side_effect=_verify)
    return handler


@pytest.mark.asyncio
async def test_verify_keeps_mention_order_and_reports_unverified():
    handler = _handler(['acme/api', 'acme/docs'])
    verified, unverified = await verify_mentioned_repos(
        handler, ['acme/docs', 'acme/missing', 'acme/api'], log_prefix='[test]'
    )
    assert [r.full_name for r in verified] == ['acme/docs', 'acme/api']
    assert unverified == ['acme/missing']
    handler.verify_repo_provider.assert_any_await('acme/docs', is_optional=True)


@pytest.mark.asyncio
async def test_verify_counts_duplicate_forms_once():
    handler = _handler(['acme/api'])
    verified, unverified = await verify_mentioned_repos(
        handler, ['ACME/api', 'acme/api'], log_prefix='[test]'
    )
    assert [r.full_name for r in verified] == ['acme/api']
    assert unverified == []


def test_select_excludes_primary_and_reports_only_reportable():
    selection = select_additional_repositories(
        'acme/web',
        [_repo('acme/web'), _repo('acme/api')],
        ['acme/x', 'b/y'],
        ['acme/x'],
    )
    assert [r.full_name for r in selection.additional] == ['acme/api']
    assert selection.inaccessible == ['acme/x']
    assert selection.skipped == []


def test_select_caps_additional_repositories():
    names = [f'acme/r{i}' for i in range(MAX_ADDITIONAL_REPOSITORIES + 2)]
    selection = select_additional_repositories(
        'acme/web', [_repo(n) for n in names], [], []
    )
    assert [r.full_name for r in selection.additional] == names[
        :MAX_ADDITIONAL_REPOSITORIES
    ]
    assert selection.skipped == names[MAX_ADDITIONAL_REPOSITORIES:]
    assert (
        f'at most {MAX_ADDITIONAL_REPOSITORIES + 1} repositories' in selection.notes()
    )


def test_notes_and_join_names():
    assert MentionedRepositories().notes() == ''
    assert (
        'Could not access: acme/x.'
        in MentionedRepositories(inaccessible=['acme/x']).notes()
    )
    assert join_names(['a']) == 'a'
    assert join_names(['a', 'b']) == 'a and b'
    assert join_names(['a', 'b', 'c']) == 'a, b and c'
