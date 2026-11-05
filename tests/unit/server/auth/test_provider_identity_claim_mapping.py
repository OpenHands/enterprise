"""``map_oauth_claims``: an OAuth provider's ``attribute_mapping`` picks the
userinfo claims a login's identity is read from."""

from __future__ import annotations

import pytest

from server.auth.provider_identity import map_oauth_claims

# Jira Data Center's ``/rest/api/2/myself``: no ``sub`` / ``id`` / ``email``.
JIRA_DC_USERINFO = {
    'self': 'https://jira.example.com/rest/api/2/user?username=tofarr',
    'key': 'JIRAUSER10501',
    'name': 'tofarr',
    'emailAddress': 'tim@example.com',
    'displayName': "Tim O'Farrell",
    'active': True,
}


def test_mapping_reads_jira_dc_key_and_email_address():
    claims = map_oauth_claims(JIRA_DC_USERINFO, {'sub': 'key', 'email': 'emailAddress'})

    assert claims == {
        'sub': 'JIRAUSER10501',
        'email': 'tim@example.com',
        'email_verified': None,
    }


def test_unmapped_jira_dc_userinfo_has_no_subject():
    claims = map_oauth_claims(JIRA_DC_USERINFO, {})

    assert claims == {'sub': None, 'email': None, 'email_verified': None}


@pytest.mark.parametrize(
    'userinfo, expected_sub',
    [
        ({'sub': 'oidc-sub', 'id': 7}, 'oidc-sub'),
        ({'id': 42}, 42),
        ({'user_id': 'u-1'}, 'u-1'),
    ],
    ids=['sub', 'github-id', 'user_id'],
)
def test_default_subject_claims(userinfo, expected_sub):
    assert map_oauth_claims(userinfo, {})['sub'] == expected_sub


def test_mapped_claim_missing_does_not_fall_back_to_defaults():
    claims = map_oauth_claims(
        {'sub': 'oidc-sub', 'email': 'a@example.com'},
        {'sub': 'key', 'email': 'emailAddress'},
    )

    assert claims['sub'] is None
    assert claims['email'] is None


def test_email_verified_can_be_mapped_and_keeps_false():
    claims = map_oauth_claims(
        {'sub': 's', 'verified': False, 'email_verified': True},
        {'email_verified': 'verified'},
    )

    assert claims['email_verified'] is False
