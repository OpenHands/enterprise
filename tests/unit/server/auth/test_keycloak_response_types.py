import pytest
from pydantic import JsonValue

from server.auth.keycloak_response_types import (
    ADMIN_USER,
    REFRESH_TOKENS,
    parse_keycloak_response,
    parse_stored_token_envelope,
)


def test_keycloak_identity_preserves_additional_claims() -> None:
    payload: JsonValue = {
        'id': 'account-id',
        'email': 'person@example.com',
        'attributes': {'github_id': ['42']},
        'custom_claim': 'preserved',
    }
    assert parse_keycloak_response(ADMIN_USER, payload) == payload


@pytest.mark.parametrize(
    'payload',
    [
        {'access_token': 'secret-access'},
        {'access_token': 'secret-access', 'refresh_token': 123},
    ],
)
def test_invalid_token_response_does_not_expose_tokens(payload: JsonValue) -> None:
    with pytest.raises(ValueError, match='^Invalid Keycloak response$') as error:
        parse_keycloak_response(REFRESH_TOKENS, payload)
    assert 'secret-access' not in str(error.value)


def test_stored_token_envelope_requires_encrypted_text() -> None:
    assert parse_stored_token_envelope('{"tokens":"encrypted"}') == {
        'tokens': 'encrypted'
    }
    with pytest.raises(ValueError, match='^Invalid stored Keycloak token envelope$'):
        parse_stored_token_envelope('{"tokens":{"refresh_token":"secret"}}')
