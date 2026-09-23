"""Validate the rendered installation, without starting application containers."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope='module')
def services():
    result = subprocess.run(
        [
            'docker',
            'compose',
            '-f',
            str(ROOT / 'containers/compose/compose.yaml'),
            'config',
            '--format',
            'json',
        ],
        env={
            **os.environ,
            'ENTERPRISE_DB_PASSWORD': 'contract-placeholder',
            'AUTOMATION_DB_PASSWORD': 'contract-placeholder',
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)['services']


def test_applications_start_independently_after_their_migrations(services):
    for app in ('enterprise', 'automation'):
        dependencies = services[app]['depends_on']
        assert (
            dependencies[f'{app}-migrate']['condition']
            == 'service_completed_successfully'
        )
        assert ({'enterprise', 'automation'} - {app}).isdisjoint(dependencies)
        assert services[app]['restart'] == 'unless-stopped'


def test_databases_and_storage_have_separate_failure_boundaries(services):
    first = services['enterprise-db']
    second = services['automation-db']
    assert first['volumes'][0]['source'] != second['volumes'][0]['source']
    for name, service in services.items():
        assert not service.get('ports'), name
        assert float(service['cpus']) > 0, name
        assert int(service['mem_limit']) > 0, name
    assert services['enterprise']['environment']['DB_HOST'] == 'enterprise-db'
    assert (
        services['automation']['environment']['AUTOMATION_DB_HOST'] == 'automation-db'
    )


def test_automation_uses_enterprise_identity_and_preserves_callback_prefix(services):
    env = services['automation']['environment']
    assert env['AUTOMATION_OPENHANDS_API_BASE_URL'] == 'http://enterprise:3000'
    assert not env.get('AUTOMATION_LOCAL_API_KEY')
    assert not env.get('AUTOMATION_AGENT_SERVER_URL')
    assert '/api/automation' not in env['AUTOMATION_BASE_URL']
    assert (
        services['enterprise']['environment']['AUTOMATION_SERVICE_URL']
        == 'http://automation:8000/api/automation'
    )
