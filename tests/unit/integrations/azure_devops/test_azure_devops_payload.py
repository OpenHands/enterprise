import pytest

from integrations.azure_devops.azure_devops_payload import extract_org


@pytest.mark.parametrize(
    'payload,expected',
    [
        (
            {
                'resourceContainers': {
                    'account': {'baseUrl': 'https://dev.azure.com/fabrikam/'}
                }
            },
            'fabrikam',
        ),
        (
            {
                'resourceContainers': {
                    'collection': {'baseUrl': 'https://fabrikam.visualstudio.com/'}
                }
            },
            'fabrikam',
        ),
        (
            {
                'resource': {
                    'url': 'https://dev.azure.com/fabrikam/proj/_apis/git/'
                    'repositories/repo/pullRequests/1'
                }
            },
            'fabrikam',
        ),
        (
            {
                'resource': {
                    'pullRequest': {
                        'repository': {
                            'remoteUrl': 'https://dev.azure.com/fabrikam/proj/_git/repo'
                        }
                    }
                }
            },
            'fabrikam',
        ),
        ({'resource': {'url': 'https://ado.example.com/tfs/coll/_apis/wit/1'}}, ''),
        ({}, ''),
    ],
    ids=[
        'account-base-url',
        'visualstudio-host',
        'pull-request-resource-url',
        'commented-pull-request-remote-url',
        'unknown-host',
        'empty',
    ],
)
def test_extract_org(payload, expected):
    assert extract_org(payload) == expected
