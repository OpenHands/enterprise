"""Slack starts with every repository named in the message.

The picker is shown only when the message names no repository that can be
accessed; it is a multi-select whose first choice is the primary repository.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from integrations.models import Message, SourceType
from integrations.slack.slack_errors import SlackErrorCode
from integrations.slack.slack_manager import (
    SLACK_FORM_INTERACTION_KEY_PREFIX,
    SLACK_FORM_REPOS_KEY_PREFIX,
    SlackManager,
)
from integrations.slack.slack_view import (
    SlackFactory,
    SlackNewConversationFromRepoFormView,
    SlackNewConversationView,
)
from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationStartTask,
    AppConversationStartTaskStatus,
)
from openhands.app_server.integrations.service_types import (
    ProviderType,
    Repository,
)
from openhands.app_server.user_auth.user_auth import UserAuth
from storage.slack_user import SlackUser

MESSAGE_TS = '1234567890.123456'


def _repo(full_name: str, provider: ProviderType = ProviderType.GITHUB) -> Repository:
    return Repository(
        id=full_name, full_name=full_name, git_provider=provider, is_public=False
    )


def _verifier(accessible: dict[str, Repository]):
    async def verify(name, specified_provider=None, is_optional=False):
        if name in accessible:
            return accessible[name]
        raise Exception(f'{name} not found')

    return verify


@pytest.fixture
def slack_manager():
    return SlackManager(token_manager=MagicMock())


@pytest.fixture
def slack_user():
    user = SlackUser()
    user.slack_user_id = 'U123'
    user.keycloak_user_id = 'kc-user'
    user.slack_display_name = 'Test User'
    return user


@pytest.fixture
def user_auth():
    auth = MagicMock(spec=UserAuth)
    auth.get_provider_tokens = AsyncMock(return_value={'github': 'token'})
    auth.get_access_token = AsyncMock(return_value='access-token')
    auth.get_user_id = AsyncMock(return_value='user-123')
    return auth


def _view_kwargs(slack_user, user_auth, user_msg: str, selected_repo=None) -> dict:
    return dict(
        bot_access_token='xoxb-test',
        user_msg=user_msg,
        slack_user_id='U123',
        slack_to_openhands_user=slack_user,
        saas_user_auth=user_auth,
        channel_id='C123',
        message_ts=MESSAGE_TS,
        thread_ts=None,
        selected_repo=selected_repo,
        should_extract=True,
        send_summary_instruction=True,
        conversation_id='',
        team_id='T123',
    )


@pytest.fixture
def make_view(slack_user, user_auth):
    def make(user_msg: str) -> SlackNewConversationView:
        return SlackNewConversationView(**_view_kwargs(slack_user, user_auth, user_msg))

    return make


@pytest.fixture
def provider_handler():
    with patch('integrations.slack.slack_manager.ProviderHandler') as cls:
        handler = MagicMock()
        cls.return_value = handler
        yield handler


@pytest.fixture
def redis():
    with patch('integrations.slack.slack_manager.get_redis_client_async') as get:
        client = AsyncMock()
        get.return_value = client
        yield client


def _actions(blocks: list[dict]) -> list[dict]:
    return next(b for b in blocks if b['type'] == 'actions')['elements']


class TestMentionedRepositories:
    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_several_verified_mentions_start_without_picker(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        provider_handler.verify_repo_provider = AsyncMock(
            side_effect=_verifier(
                {
                    'acme/api': _repo('acme/api'),
                    'acme/web': _repo('acme/web', ProviderType.GITLAB),
                    'acme/docs': _repo('acme/docs'),
                }
            )
        )
        view = make_view('Please fix acme/api, acme/web and acme/docs')

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        mock_send_message.assert_not_called()
        redis.set.assert_not_called()
        assert view.selected_repo == 'acme/api'
        assert [r.full_name for r in view.additional_repos] == [
            'acme/web',
            'acme/docs',
        ]
        assert view.inaccessible_repos == []
        assert view.skipped_repos == []

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_unverified_mention_is_reported_and_does_not_stop_start(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        provider_handler.verify_repo_provider = AsyncMock(
            side_effect=_verifier(
                {'acme/api': _repo('acme/api'), 'acme/web': _repo('acme/web')}
            )
        )
        view = make_view('Fix acme/missing, acme/api and acme/web')

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        mock_send_message.assert_not_called()
        # The first *verified* mention is the primary.
        assert view.selected_repo == 'acme/api'
        assert [r.full_name for r in view.additional_repos] == ['acme/web']
        assert view.inaccessible_repos == ['acme/missing']

        view.conversation_id = 'abc'
        reply = view.get_response_msg()
        assert reply.startswith("I'm on it in acme/api, together with acme/web! ")
        assert 'Could not access: acme/missing.' in reply

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_first_named_repository_is_primary_before_a_later_link(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        provider_handler.verify_repo_provider = AsyncMock(
            side_effect=_verifier(
                {'acme/api': _repo('acme/api'), 'psf/requests': _repo('psf/requests')}
            )
        )
        view = make_view(
            '<@U123> fix the login bug in acme/api. Upstream report: '
            '<https://github.com/psf/requests/issues/6000>'
        )

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        assert view.selected_repo == 'acme/api'
        assert [r.full_name for r in view.additional_repos] == ['psf/requests']

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_bitbucket_dc_browse_url_keeps_its_position(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        provider_handler.verify_repo_provider = AsyncMock(
            side_effect=_verifier(
                {
                    'ENG/api': _repo('ENG/api', ProviderType.BITBUCKET_DATA_CENTER),
                    'ENG/web': _repo('ENG/web', ProviderType.BITBUCKET_DATA_CENTER),
                }
            )
        )
        view = make_view(
            '<@U1> fix https://bitbucket.acme.com/projects/ENG/repos/api/browse/src/x.py'
            ' and update ENG/web'
        )

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        assert view.selected_repo == 'ENG/api'
        assert [r.full_name for r in view.additional_repos] == ['ENG/web']

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_slack_permalink_is_not_a_repository(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        provider_handler.verify_repo_provider = AsyncMock(
            side_effect=_verifier(
                {'acme/web': _repo('acme/web'), 'acme/api': _repo('acme/api')}
            )
        )
        view = make_view(
            '<@U123> please update acme/web and acme/api, see '
            '<https://openhands.slack.com/archives/C0123/p1700000|thread>'
        )

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        assert view.selected_repo == 'acme/web'
        assert view.inaccessible_repos == []
        verified = [
            c.args[0] for c in provider_handler.verify_repo_provider.call_args_list
        ]
        assert verified == ['acme/web', 'acme/api']

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_single_verified_mention_is_unchanged(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        provider_handler.verify_repo_provider = AsyncMock(
            return_value=_repo('OpenHands/OpenHands')
        )
        view = make_view('Help me with OpenHands/OpenHands repo')

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        mock_send_message.assert_not_called()
        assert view.selected_repo == 'OpenHands/OpenHands'
        assert view.additional_repos == []
        assert view.inaccessible_repos == []

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_two_forms_of_one_repository_count_once(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        provider_handler.verify_repo_provider = AsyncMock(
            return_value=_repo('acme/api')
        )
        view = make_view('Fix acme/api, see https://github.com/ACME/api')

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        assert view.selected_repo == 'acme/api'
        assert view.additional_repos == []

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_mentions_beyond_the_limit_are_skipped_and_reported(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        names = [f'acme/r{i}' for i in range(12)]
        provider_handler.verify_repo_provider = AsyncMock(
            side_effect=_verifier({n: _repo(n) for n in names})
        )
        view = make_view('Update ' + ' '.join(names))

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        assert view.selected_repo == 'acme/r0'
        assert [r.full_name for r in view.additional_repos] == names[1:10]
        assert view.skipped_repos == names[10:]
        view.conversation_id = 'abc'
        assert (
            'Not included (a conversation can have at most 10 repositories): '
            'acme/r10, acme/r11.'
        ) in view.get_response_msg()

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_no_mention_shows_multi_select_picker(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        view = make_view('Hello, can you help me?')

        assert await slack_manager.is_job_requested(MagicMock(), view) is False

        provider_handler.verify_repo_provider.assert_not_called()
        mock_send_message.assert_called_once()
        message, _ = mock_send_message.call_args[0]
        assert mock_send_message.call_args.kwargs == {'ephemeral': True}
        elements = _actions(message['blocks'])
        assert [e['type'] for e in elements] == [
            'button',
            'multi_external_select',
            'button',
        ]
        # The user message is kept for the form submission.
        redis.set.assert_awaited_once()

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_no_accessible_mention_shows_picker_naming_them(
        self, mock_send_message, slack_manager, make_view, provider_handler, redis
    ):
        provider_handler.verify_repo_provider = AsyncMock(side_effect=_verifier({}))
        view = make_view('Fix acme/missing and client/server')

        assert await slack_manager.is_job_requested(MagicMock(), view) is False

        message, _ = mock_send_message.call_args[0]
        prompt = message['blocks'][1]['text']['text']
        assert prompt.startswith('Could not access: acme/missing, client/server. ')

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_no_provider_tokens_shows_picker(
        self,
        mock_send_message,
        slack_manager,
        make_view,
        user_auth,
        provider_handler,
        redis,
    ):
        user_auth.get_provider_tokens = AsyncMock(return_value=None)
        view = make_view('Fix acme/api')

        assert await slack_manager.is_job_requested(MagicMock(), view) is False

        provider_handler.verify_repo_provider.assert_not_called()
        mock_send_message.assert_called_once()


class TestRepositorySelectionForm:
    def test_form_structure(self, slack_manager):
        blocks = slack_manager._generate_repo_selection_form(MESSAGE_TS, None)

        assert blocks[0] == {
            'type': 'header',
            'text': {
                'type': 'plain_text',
                'text': 'Choose repositories',
                'emoji': True,
            },
        }
        assert blocks[1]['type'] == 'section'
        assert 'Could not access' not in blocks[1]['text']['text']
        no_repo, select, start = _actions(blocks)
        assert no_repo == {
            'type': 'button',
            'action_id': f'no_repository:{MESSAGE_TS}:None',
            'text': {'type': 'plain_text', 'text': 'No Repository', 'emoji': True},
            'value': '-',
        }
        assert select == {
            'type': 'multi_external_select',
            'action_id': f'repositories_select:{MESSAGE_TS}:None',
            'placeholder': {'type': 'plain_text', 'text': 'Search repositories...'},
            'min_query_length': 0,
            'max_selected_items': 10,
        }
        assert start['type'] == 'button'
        assert start['action_id'] == f'start_with_repositories:{MESSAGE_TS}:None'
        assert start['style'] == 'primary'
        # Slack requires unique action_ids within a message.
        assert len({e['action_id'] for e in _actions(blocks)}) == 3

    def test_form_in_thread_carries_thread_ts(self, slack_manager):
        blocks = slack_manager._generate_repo_selection_form(MESSAGE_TS, '111.222')

        for element in _actions(blocks):
            assert element['action_id'].endswith(f':{MESSAGE_TS}:111.222')


def _interaction(action: dict, state: dict | None = None) -> dict:
    payload = {
        'type': 'block_actions',
        'actions': [action],
        'user': {'id': 'U123'},
        'container': {'channel_id': 'C123'},
        'team': {'id': 'T123'},
        'response_url': 'https://hooks.slack.com/actions/test',
    }
    if state is not None:
        payload['state'] = state
    return payload


def _options(*names: str) -> list[dict]:
    return [{'text': {'type': 'plain_text', 'text': n}, 'value': n} for n in names]


START = {
    'action_id': f'start_with_repositories:{MESSAGE_TS}:None',
    'type': 'button',
    'value': '-',
}


class TestFormInteraction:
    async def test_multi_select_change_stores_selection_without_starting(
        self, slack_manager, redis
    ):
        action = {
            'action_id': f'repositories_select:{MESSAGE_TS}:None',
            'type': 'multi_external_select',
            'selected_options': _options('acme/web', 'acme/api'),
        }

        with (
            patch.object(
                slack_manager, 'receive_message', new_callable=AsyncMock
            ) as mock_receive,
            patch.object(
                slack_manager, '_replace_repo_selection_form', new_callable=AsyncMock
            ) as mock_replace,
        ):
            await slack_manager.receive_form_interaction(_interaction(action))

        mock_receive.assert_not_called()
        mock_replace.assert_not_called()
        redis.set.assert_awaited_once_with(
            f'{SLACK_FORM_REPOS_KEY_PREFIX}:{MESSAGE_TS}:None',
            json.dumps(['acme/web', 'acme/api']),
            ex=300,
        )

    async def test_start_uses_form_state_first_choice_is_primary(
        self, slack_manager, redis
    ):
        redis.set = AsyncMock(return_value=True)
        redis.get = AsyncMock(return_value=b'Hello, help me with code')
        state = {
            'values': {
                'block-1': {
                    f'repositories_select:{MESSAGE_TS}:None': {
                        'type': 'multi_external_select',
                        'selected_options': _options(
                            'acme/web', 'acme/api', 'acme/docs'
                        ),
                    }
                }
            }
        }

        with (
            patch.object(
                slack_manager, 'receive_message', new_callable=AsyncMock
            ) as mock_receive,
            patch.object(
                slack_manager, '_replace_repo_selection_form', new_callable=AsyncMock
            ) as mock_replace,
        ):
            await slack_manager.receive_form_interaction(_interaction(START, state))

        message = mock_receive.call_args[0][0]
        assert message.message['selected_repo'] == 'acme/web'
        assert message.message['additional_repos'] == ['acme/api', 'acme/docs']
        assert message.message['user_msg'] == 'Hello, help me with code'
        mock_replace.assert_awaited_once_with(
            'https://hooks.slack.com/actions/test',
            ['acme/web', 'acme/api', 'acme/docs'],
        )
        redis.set.assert_any_call(
            f'{SLACK_FORM_INTERACTION_KEY_PREFIX}:T123:C123:{MESSAGE_TS}:None',
            'processing',
            ex=300,
            nx=True,
        )

    async def test_start_without_form_state_uses_stored_selection(
        self, slack_manager, redis
    ):
        redis.set = AsyncMock(return_value=True)
        stored = {
            f'{SLACK_FORM_REPOS_KEY_PREFIX}:{MESSAGE_TS}:None': json.dumps(
                ['acme/api', 'acme/web']
            ),
        }
        redis.get = AsyncMock(side_effect=lambda key: stored.get(key, b'Fix it'))

        with (
            patch.object(
                slack_manager, 'receive_message', new_callable=AsyncMock
            ) as mock_receive,
            patch.object(
                slack_manager, '_replace_repo_selection_form', new_callable=AsyncMock
            ),
        ):
            await slack_manager.receive_form_interaction(_interaction(START))

        message = mock_receive.call_args[0][0]
        assert message.message['selected_repo'] == 'acme/api'
        assert message.message['additional_repos'] == ['acme/web']

    async def test_start_with_nothing_selected_asks_again(self, slack_manager, redis):
        redis.get = AsyncMock(return_value=None)
        state = {
            'values': {
                'block-1': {
                    f'repositories_select:{MESSAGE_TS}:None': {
                        'type': 'multi_external_select',
                        'selected_options': [],
                    }
                }
            }
        }

        with (
            patch.object(
                slack_manager, 'receive_message', new_callable=AsyncMock
            ) as mock_receive,
            patch.object(
                slack_manager, 'handle_slack_error', new_callable=AsyncMock
            ) as mock_error,
        ):
            await slack_manager.receive_form_interaction(_interaction(START, state))

        mock_receive.assert_not_called()
        # The form stays usable: the interaction is not claimed.
        redis.set.assert_not_called()
        error = mock_error.call_args[0][1]
        assert error.code == SlackErrorCode.NO_REPOSITORY_SELECTED

    async def test_no_repository_button_starts_without_repositories(
        self, slack_manager, redis
    ):
        redis.set = AsyncMock(return_value=True)
        redis.get = AsyncMock(return_value=b'Just a question')
        action = {
            'action_id': f'no_repository:{MESSAGE_TS}:None',
            'type': 'button',
            'value': '-',
        }

        with (
            patch.object(
                slack_manager, 'receive_message', new_callable=AsyncMock
            ) as mock_receive,
            patch.object(
                slack_manager, '_replace_repo_selection_form', new_callable=AsyncMock
            ) as mock_replace,
        ):
            await slack_manager.receive_form_interaction(_interaction(action))

        message = mock_receive.call_args[0][0]
        assert message.message['selected_repo'] is None
        assert message.message['additional_repos'] == []
        mock_replace.assert_awaited_once_with(
            'https://hooks.slack.com/actions/test', []
        )

    async def test_single_select_from_an_earlier_form_still_starts(
        self, slack_manager, redis
    ):
        redis.set = AsyncMock(return_value=True)
        redis.get = AsyncMock(return_value=b'Fix it')
        action = {
            'action_id': f'repository_select:{MESSAGE_TS}:None',
            'type': 'external_select',
            'selected_option': {'value': 'acme/api'},
        }

        with (
            patch.object(
                slack_manager, 'receive_message', new_callable=AsyncMock
            ) as mock_receive,
            patch.object(
                slack_manager, '_replace_repo_selection_form', new_callable=AsyncMock
            ),
        ):
            await slack_manager.receive_form_interaction(_interaction(action))

        message = mock_receive.call_args[0][0]
        assert message.message['selected_repo'] == 'acme/api'
        assert message.message['additional_repos'] == []

    async def test_replaced_form_names_every_selected_repository(self, slack_manager):
        with patch('integrations.slack.slack_manager.httpx.AsyncClient') as client:
            post = AsyncMock()
            client.return_value.__aenter__.return_value.post = post
            await slack_manager._replace_repo_selection_form(
                'https://hooks.slack.com/x', ['acme/api', 'acme/web']
            )

        sent = post.call_args.kwargs['json']
        assert sent['text'] == (
            'Repositories selected: `acme/api`, `acme/web`. Starting conversation...'
        )


class TestFormSelectionView:
    @patch('integrations.slack.slack_view.slack_conversation_store')
    @patch('integrations.slack.slack_view.slack_team_store')
    async def test_factory_carries_the_picked_list(
        self, mock_team_store, mock_convo_store, slack_user, user_auth
    ):
        mock_team_store.get_team_bot_token = AsyncMock(return_value='xoxb')
        mock_convo_store.get_slack_conversation = AsyncMock(return_value=None)
        message = Message(
            source=SourceType.SLACK,
            message={
                'team_id': 'T123',
                'channel_id': 'C123',
                'slack_user_id': 'U123',
                'message_ts': MESSAGE_TS,
                'thread_ts': None,
                'selected_repo': 'acme/web',
                'additional_repos': ['acme/api'],
                'user_msg': 'Fix it',
            },
        )

        view = await SlackFactory.create_slack_view_from_payload(
            message, slack_user, user_auth
        )

        assert isinstance(view, SlackNewConversationFromRepoFormView)
        assert view.selected_repo == 'acme/web'
        assert view.requested_additional_repos == ['acme/api']

    @patch.object(SlackManager, 'send_message', new_callable=AsyncMock)
    async def test_picked_repositories_are_verified_and_cloned_beside_primary(
        self,
        mock_send_message,
        slack_manager,
        slack_user,
        user_auth,
        provider_handler,
    ):
        provider_handler.verify_repo_provider = AsyncMock(
            side_effect=_verifier(
                {'acme/api': _repo('acme/api'), 'ACME/web': _repo('acme/web')}
            )
        )
        view = SlackNewConversationFromRepoFormView(
            **_view_kwargs(slack_user, user_auth, 'Fix it', selected_repo='acme/web'),
            requested_additional_repos=['acme/api', 'acme/gone', 'ACME/web'],
        )

        assert await slack_manager.is_job_requested(MagicMock(), view) is True

        mock_send_message.assert_not_called()
        assert view.selected_repo == 'acme/web'
        assert [r.full_name for r in view.additional_repos] == ['acme/api']
        assert view.inaccessible_repos == ['acme/gone']


class TestStartRequest:
    @patch('integrations.slack.slack_view.resolve_org_for_repo', new_callable=AsyncMock)
    @patch('integrations.slack.slack_view.ProviderHandler')
    @patch('integrations.slack.slack_view.get_app_conversation_service')
    @patch.object(SlackNewConversationView, 'save_slack_convo', new_callable=AsyncMock)
    @patch.object(SlackNewConversationView, '_get_instructions', new_callable=AsyncMock)
    async def test_start_request_carries_additional_repositories(
        self,
        mock_get_instructions,
        mock_save,
        mock_get_service,
        mock_provider_handler_cls,
        mock_resolve_org,
        make_view,
    ):
        mock_get_instructions.return_value = ('Fix it', '')
        mock_provider_handler_cls.return_value.verify_repo_provider = AsyncMock(
            return_value=_repo('acme/api')
        )
        mock_resolve_org.return_value = uuid4()
        requests = []

        async def start_app_conversation(request):
            requests.append(request)
            yield AppConversationStartTask(
                created_by_user_id='kc-user',
                request=request,
                status=AppConversationStartTaskStatus.READY,
            )

        mock_get_service.return_value.__aenter__.return_value.start_app_conversation = (
            start_app_conversation
        )
        view = make_view('Fix acme/api and acme/web')
        view.selected_repo = 'acme/api'
        view.additional_repos = [_repo('acme/web', ProviderType.GITLAB)]

        await view.create_or_update_conversation(MagicMock())

        (request,) = requests
        assert request.selected_repository == 'acme/api'
        assert request.git_provider == ProviderType.GITHUB
        assert [
            (r.repository, r.git_provider) for r in request.additional_repositories
        ] == [('acme/web', ProviderType.GITLAB)]
        assert request.require_all is False

    @patch('integrations.slack.slack_view.resolve_org_for_repo', new_callable=AsyncMock)
    @patch('integrations.slack.slack_view.ProviderHandler')
    @patch('integrations.slack.slack_view.get_app_conversation_service')
    @patch.object(SlackNewConversationView, 'save_slack_convo', new_callable=AsyncMock)
    @patch.object(SlackNewConversationView, '_get_instructions', new_callable=AsyncMock)
    async def test_single_repository_request_has_no_additional_repositories(
        self,
        mock_get_instructions,
        mock_save,
        mock_get_service,
        mock_provider_handler_cls,
        mock_resolve_org,
        make_view,
    ):
        mock_get_instructions.return_value = ('Fix it', '')
        mock_provider_handler_cls.return_value.verify_repo_provider = AsyncMock(
            return_value=_repo('acme/api')
        )
        mock_resolve_org.return_value = None
        requests = []

        async def start_app_conversation(request):
            requests.append(request)
            yield AppConversationStartTask(
                created_by_user_id='kc-user',
                request=request,
                status=AppConversationStartTaskStatus.READY,
            )

        mock_get_service.return_value.__aenter__.return_value.start_app_conversation = (
            start_app_conversation
        )
        view = make_view('Fix acme/api')
        view.selected_repo = 'acme/api'

        await view.create_or_update_conversation(MagicMock())

        (request,) = requests
        assert request.selected_repository == 'acme/api'
        assert request.additional_repositories is None


class TestResponseMessage:
    def test_names_every_repository(self, make_view):
        view = make_view('Fix it')
        view.conversation_id = 'abc'
        view.selected_repo = 'acme/api'
        view.additional_repos = [_repo('acme/web'), _repo('acme/docs')]

        assert view.get_response_msg().startswith(
            "I'm on it in acme/api, together with acme/web and acme/docs! "
            'Test User can [track my progress here]('
        )

    def test_single_repository(self, make_view):
        view = make_view('Fix it')
        view.conversation_id = 'abc'
        view.selected_repo = 'acme/api'

        reply = view.get_response_msg()
        assert reply.startswith("I'm on it in acme/api! Test User can [track")
        assert '\n' not in reply

    def test_no_repository(self, make_view):
        view = make_view('Question')
        view.conversation_id = 'abc'

        assert view.get_response_msg().startswith(
            "I'm on it! Test User can [track my progress here]("
        )
