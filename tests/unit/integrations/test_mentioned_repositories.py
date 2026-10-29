from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.mentioned_repositories import (
    MentionedRepositories,
    join_names,
    resolve_mentioned_repositories,
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


async def _resolve(handler, comment, primary='acme/web'):
    with patch(
        'integrations.mentioned_repositories.create_provider_handler',
        AsyncMock(return_value=handler),
    ):
        return await resolve_mentioned_repositories(
            MagicMock(),
            primary=primary,
            provider=ProviderType.GITHUB,
            comment=comment,
            log_prefix='[test]',
        )


@pytest.mark.asyncio
async def test_comment_mentions_keep_their_order():
    handler = _handler(['acme/api', 'acme/docs'])
    result = await _resolve(handler, '@openhands use acme/docs and acme/api')
    assert [r.full_name for r in result.additional] == ['acme/docs', 'acme/api']


@pytest.mark.asyncio
async def test_only_inaccessible_repository_urls_are_reported():
    handler = _handler(['acme/api'])
    result = await _resolve(
        handler,
        '@openhands use acme/api; the form in src/components is broken, as in '
        'https://docs.python.org/3/library/re.html and '
        'https://stackoverflow.com/questions/12345/how-to-x; '
        'see also https://github.com/acme/missing',
    )
    assert [r.full_name for r in result.additional] == ['acme/api']
    assert result.inaccessible == ['acme/missing']


@pytest.mark.asyncio
async def test_primary_and_duplicate_forms_count_once():
    handler = _handler(['acme/api'])
    result = await _resolve(
        handler,
        '@openhands acme/web, ACME/api and https://github.com/acme/api/pull/2',
    )
    assert [r.full_name for r in result.additional] == ['acme/api']
    looked_up = [c.args[0] for c in handler.verify_repo_provider.await_args_list]
    assert 'acme/web' not in looked_up


@pytest.mark.asyncio
async def test_additional_repositories_are_capped():
    names = [f'acme/r{i}' for i in range(MAX_ADDITIONAL_REPOSITORIES + 2)]
    result = await _resolve(_handler(names), '@openhands ' + ' '.join(names))
    assert [r.full_name for r in result.additional] == names[
        :MAX_ADDITIONAL_REPOSITORIES
    ]
    assert result.skipped == names[MAX_ADDITIONAL_REPOSITORIES:]
    assert f'at most {MAX_ADDITIONAL_REPOSITORIES + 1} repositories' in result.notes()


@pytest.mark.asyncio
async def test_no_linked_provider_reports_comment_mentions():
    with patch(
        'integrations.mentioned_repositories.create_provider_handler',
        AsyncMock(return_value=None),
    ):
        result = await resolve_mentioned_repositories(
            MagicMock(),
            primary='acme/web',
            provider=ProviderType.GITHUB,
            comment='@openhands https://github.com/acme/api',
            log_prefix='[test]',
        )
    assert result.additional == []
    assert result.inaccessible == ['acme/api']


@pytest.mark.asyncio
async def test_verify_without_preferred_provider_keeps_jira_dc_call():
    handler = _handler(['acme/api'])
    await verify_mentioned_repos(handler, ['acme/api'], log_prefix='[Jira DC]')
    handler.verify_repo_provider.assert_awaited_once_with('acme/api', is_optional=True)


def test_select_additional_repositories_excludes_primary():
    selection = select_additional_repositories(
        'acme/web',
        [_repo('acme/web'), _repo('acme/api')],
        ['acme/x', 'b/y'],
        ['acme/x'],
    )
    assert [r.full_name for r in selection.additional] == ['acme/api']
    assert selection.inaccessible == ['acme/x']


def test_messages():
    assert MentionedRepositories().on_it_message('acme/web') == "I'm on it!"
    assert MentionedRepositories().additional_repository_requests() is None
    both = MentionedRepositories(additional=[_repo('acme/a'), _repo('acme/b')])
    assert both.on_it_message('acme/web') == (
        "I'm on it in acme/web, together with acme/a and acme/b!"
    )
    assert join_names(['a', 'b', 'c']) == 'a, b and c'


@pytest.mark.asyncio
async def test_inaccessible_self_hosted_url_is_reported():
    handler = _handler([])
    handler.provider_tokens = {
        ProviderType.GITLAB: MagicMock(host='https://git.example.com')
    }
    result = await _resolve(
        handler, '@openhands see https://git.example.com/team/svc/-/issues/3'
    )
    assert result.inaccessible == ['team/svc']
