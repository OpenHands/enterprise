from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from tests.integration.budgets import run_readiness
from tests.integration.budgets.services import ProviderState, create_provider_app


def test_provider_reset_does_not_reuse_completion_ids() -> None:
    with TestClient(create_provider_app(ProviderState())) as client:
        first = client.post('/v1/chat/completions').json()
        client.post('/test/reset')
        second = client.post('/v1/chat/completions').json()
        assert first['id'] != second['id']
        assert first['usage'] == second['usage']
        assert client.get('/test/requests').json()['calls'] == 1


@pytest.mark.parametrize(
    ('cases', 'pytest_exit', 'expected'),
    [
        ('<testcase/>', 0, 0),
        ('<testcase><skipped/></testcase>', 0, 1),
        ('', 0, 1),
        ('<testcase><failure/></testcase>', 1, 1),
    ],
)
def test_readiness_rejects_incomplete_runs(
    tmp_path, monkeypatch, cases, pytest_exit, expected
) -> None:
    report = tmp_path / 'result.xml'
    monkeypatch.setattr(sys, 'argv', ['run_readiness', '--junitxml', str(report)])
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k nonexistent')

    def run(command, **kwargs):
        assert kwargs['env']['PYTEST_ADDOPTS'] == ''
        assert any(item.endswith('probe_fail_closed.py') for item in command)
        assert any(item.endswith('probe_budget_api.py') for item in command)
        report.write_text(f'<testsuites><testsuite>{cases}</testsuite></testsuites>')
        return subprocess.CompletedProcess(command, pytest_exit)

    monkeypatch.setattr(run_readiness.subprocess, 'run', run)
    assert run_readiness.main() == expected
