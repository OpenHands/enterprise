"""Real HTTP and toolkit boundaries for management response validation."""

import hashlib
import traceback
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, JsonValue, SecretStr

from storage.lite_llm_manager import LiteLlmManager
from storage.litellm_models import TEAM_RESPONSE, LiteLlmMember, parse_response


@pytest.fixture(autouse=True)
def gateway_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('ENABLE_LITELLM', 'true')
    monkeypatch.setattr('storage.lite_llm_manager.LITE_LLM_API_KEY', 'test-master')
    monkeypatch.setattr(
        'storage.lite_llm_manager.LITE_LLM_API_URL', 'https://gateway.invalid'
    )


@pytest.mark.parametrize('budget', [None, 12.0])
def test_budget_response_preserves_missing_and_explicit_null(
    budget: float | None,
) -> None:
    missing = parse_response(
        httpx.Response(200, json={'team_info': {'spend': 3.0}}), TEAM_RESPONSE
    )
    complete = parse_response(
        httpx.Response(200, json={'team_info': {'max_budget': budget, 'spend': 3.0}}),
        TEAM_RESPONSE,
    )
    assert 'max_budget' not in missing['team_info']
    assert complete['team_info']['max_budget'] == budget


class ToolkitMember(BaseModel):
    user_id: str
    spend: float | None = None


def test_toolkit_and_serialized_roster_records_preserve_field_presence() -> None:
    response = TEAM_RESPONSE.validate_python(
        {
            'team_memberships': [
                ToolkitMember(user_id='toolkit'),
                '{"user_id":"serialized","spend":null}',
                'legacy-id',
            ]
        }
    )
    members = response['team_memberships']
    assert members is not None
    assert members == [
        {'user_id': 'toolkit'},
        {'user_id': 'serialized', 'spend': None},
        {'user_id': 'legacy-id'},
    ]


@pytest.mark.parametrize(
    'invalid', [True, 'secret-in-invalid-number', float('inf'), float('nan')]
)
def test_invalid_financial_wire_values_are_redacted(invalid: JsonValue) -> None:
    # Response.content can contain nonstandard NaN; httpx's serializer correctly
    # refuses that JSON, so construct its bytes at this external boundary.
    if isinstance(invalid, float):
        content = (
            b'{"team_info":{"spend":NaN}}'
            if invalid != invalid
            else b'{"team_info":{"spend":Infinity}}'
        )
        response = httpx.Response(200, content=content)
    else:
        response = httpx.Response(
            200,
            json={
                'team_info': {'spend': invalid},
                'credential': 'hidden-master-secret',
            },
        )
    with pytest.raises(
        ValueError, match='Invalid LiteLLM management response'
    ) as error:
        parse_response(response, TEAM_RESPONSE)
    rendered = ''.join(traceback.format_exception(error.value))
    assert 'hidden-master-secret' not in rendered
    assert 'secret-in-invalid-number' not in rendered
    assert error.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_real_http_roster_uses_membership_and_validated_key_spend() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/team/info'
        return httpx.Response(
            200,
            json={
                'team_info': {
                    'max_budget': None,
                    'spend': 10.0,
                    'members_with_roles': ['budgeted', 'role-only'],
                },
                'team_memberships': [
                    {
                        'user_id': 'budgeted',
                        'spend': 4.0,
                        'budget_id': 'private',
                        'litellm_budget_table': {'max_budget': 20.0},
                    }
                ],
                'keys': [
                    {'user_id': 'budgeted', 'spend': 999.0},
                    {'user_id': 'role-only', 'spend': 2.0},
                    {'user_id': 'role-only', 'spend': 4.0},
                ],
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        financial = await LiteLlmManager._get_team_members_financial_data(http, 'team')
    assert financial['members']['budgeted'] == {
        'spend': 4.0,
        'max_budget': 20.0,
        'uses_shared_budget': False,
    }
    assert financial['members']['role-only'] == {
        'spend': 6.0,
        'max_budget': None,
        'uses_shared_budget': True,
    }


@pytest.mark.asyncio
async def test_malformed_key_ownership_response_preserves_permissive_and_strict_modes() -> (
    None
):
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={'keys': [{'key_name': ['hidden-key']}]}, request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        assert (
            await LiteLlmManager._verify_existing_key(
                http, 'existing-key', 'user', 'org'
            )
            is True
        )
        with pytest.raises(RuntimeError):
            await LiteLlmManager._verify_existing_key_strict(
                http, 'existing-key', 'user', 'org'
            )


@pytest.mark.asyncio
async def test_key_generation_failure_logs_no_response_body() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, json={'error': 'hidden-key-in-error'}, request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with patch('storage.lite_llm_manager.logger') as logger:
            with pytest.raises(httpx.HTTPStatusError):
                await LiteLlmManager._generate_key(http, 'user', 'org', 'alias', None)
    assert 'hidden-key-in-error' not in str(logger.mock_calls)


def test_unknown_member_balance_remains_unknown() -> None:
    member: LiteLlmMember = {'user_id': 'member', 'spend': 4.0}
    assert LiteLlmManager.get_budget_from_team_info(member, 'member', 'org') is None


@pytest.mark.asyncio
async def test_key_financial_lookup_uses_hashed_key_and_real_member_identity() -> None:
    from storage.org_member import OrgMember
    from storage.user import User

    user_id, org_id = uuid4(), uuid4()
    raw_key = 'sk-unmasked-management-key'
    user = User(id=user_id, current_org_id=org_id)
    member = OrgMember(org_id=org_id, user_id=user_id, llm_api_key=SecretStr(raw_key))
    user.org_members = [member]

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.params['key'] == hashlib.sha256(raw_key.encode()).hexdigest()
        assert raw_key not in str(request.url)
        return httpx.Response(
            200, json={'info': {'max_budget': None, 'spend': 3.0}}, request=request
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        with patch(
            'storage.user_store.UserStore.get_user_by_id', AsyncMock(return_value=user)
        ):
            result = await LiteLlmManager._get_key_info(http, str(org_id), str(user_id))
    assert result == {'key_max_budget': None, 'key_spend': 3.0}


def test_keycloak_response_contracts_preserve_admin_attributes_and_reject_bad_tokens() -> (
    None
):
    from server.auth.keycloak_response_types import (
        ADMIN_USER,
        REFRESH_TOKENS,
        parse_keycloak_response,
    )

    user = ADMIN_USER.validate_python(
        {
            'id': 'user',
            'email': 'user@example.com',
            'attributes': {'github_id': ['42']},
            'requiredActions': ['VERIFY_EMAIL'],
        }
    )
    assert user['attributes'] == {'github_id': ['42']}
    assert user['requiredActions'] == ['VERIFY_EMAIL']
    with pytest.raises(ValueError) as error:
        parse_keycloak_response(
            REFRESH_TOKENS,
            {
                'access_token': ['hidden-access-token'],
                'refresh_token': 'hidden-refresh-token',
            },
        )
    rendered = ''.join(traceback.format_exception(error.value))
    assert 'hidden-access-token' not in rendered
    assert 'hidden-refresh-token' not in rendered
    assert error.value.__suppress_context__ is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status_code', 'content', 'expected', 'budget_blocked'),
    [
        (400, b'{"error":{"message":"Budget exceeded"}}', True, True),
        (400, b'{"detail":"Team budget exceeded"}', True, True),
        (400, b'Budget exceeded', True, True),
        (400, b'{"error":"temporarily unavailable"}', True, False),
        (401, b'{"error":"Invalid API key"}', False, False),
        (403, b'Forbidden', False, False),
        (200, b'{"data":[]}', True, False),
    ],
)
async def test_key_verification_preserves_real_json_and_text_error_classification(
    status_code: int, content: bytes, expected: bool, budget_blocked: bool
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, content=content, request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with (
        patch('storage.lite_llm_manager.httpx.AsyncClient', return_value=http),
        patch('storage.lite_llm_manager.logger') as logger,
    ):
        assert await LiteLlmManager.verify_key('sk-preserved-key', 'user') is expected
    assert len(requests) == 1
    assert requests[0].url.path == '/v1/models'
    assert requests[0].headers['Authorization'] == 'Bearer sk-preserved-key'
    if budget_blocked:
        logger.info.assert_called_once_with(
            'Key verification blocked by budget exceeded - preserving key',
            extra={'user_id': 'user', 'status_code': 400},
        )
    else:
        logger.info.assert_not_called()
    assert 'sk-preserved-key' not in str(logger.mock_calls)
