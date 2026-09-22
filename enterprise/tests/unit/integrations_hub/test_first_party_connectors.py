"""Tests for enterprise first-party Hub connectors."""

from __future__ import annotations

from integrations_hub.first_party_connectors import enterprise_first_party_connectors
from integrations_hub.managed_connectors import default_managed_connectors


def test_enterprise_first_party_connector_slugs() -> None:
    slugs = {connector.slug for connector in enterprise_first_party_connectors()}
    assert slugs == {
        'gitlab',
        'azure_devops',
        'forgejo',
        'bitbucket_data_center',
        'jira-dc',
    }


def test_default_managed_connectors_include_first_party() -> None:
    default_managed_connectors.cache_clear()
    try:
        slugs = {connector.slug for connector in default_managed_connectors()}
        for slug in (
            'gitlab',
            'azure_devops',
            'forgejo',
            'bitbucket_data_center',
            'jira-dc',
        ):
            assert slug in slugs
    finally:
        default_managed_connectors.cache_clear()
