from __future__ import annotations

import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.integration.budgets import run_readiness
from tests.integration.budgets.adapter import BudgetTestAdapter
from tests.integration.budgets.services import ProviderState, create_provider_app


@pytest.fixture(autouse=True)
def isolate_github_summary(monkeypatch) -> None:
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)


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
        ('<testcase><error/></testcase>', 0, 1),
    ],
)
def test_readiness_rejects_incomplete_runs(
    tmp_path, monkeypatch, cases, pytest_exit, expected
) -> None:
    report = tmp_path / 'result.xml'
    monkeypatch.setattr(sys, 'argv', ['run_readiness', '--junitxml', str(report)])
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k nonexistent')

    def run(command, **kwargs):
        inherited_filters = kwargs.pop('env')['PYTEST_ADDOPTS']
        assert inherited_filters == ''
        assert any(item.endswith('probe_fail_closed.py') for item in command)
        assert any(item.endswith('probe_budget_api.py') for item in command)
        report.write_text(f'<testsuites><testsuite>{cases}</testsuite></testsuites>')
        return subprocess.CompletedProcess(command, pytest_exit)

    monkeypatch.setattr(run_readiness.subprocess, 'run', run)
    assert run_readiness.main() == expected


@pytest.mark.parametrize('suite', ['regression', 'known-issues'])
def test_ci_groups_select_opposite_markers_and_report_results(
    tmp_path, monkeypatch, suite
) -> None:
    report = tmp_path / 'result.xml'
    summary = tmp_path / 'summary.md'
    monkeypatch.setattr(
        sys, 'argv', ['run_readiness', '--suite', suite, '--junitxml', str(report)]
    )
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setenv('PYTEST_ADDOPTS', '-m budget_known_issue -k nonexistent')

    def run(command, **kwargs):
        inherited_filters = kwargs.pop('env')['PYTEST_ADDOPTS']
        expression = command[command.index('-m', 3) + 1]
        assert expression == (
            'not budget_known_issue' if suite == 'regression' else 'budget_known_issue'
        )
        assert inherited_filters == ''
        report.write_text(
            '<testsuites><testsuite><testcase name="repaired_bug">'
            '<properties><property name="budget_issue" value="OHE-3318"/>'
            '</properties></testcase></testsuite></testsuites>'
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_readiness.subprocess, 'run', run)
    assert run_readiness.main() == 0
    assert 'repaired_bug | PASS' in summary.read_text()
    assert 'https://linear.app/all-hands-ai/issue/OHE-3318' in summary.read_text()


@pytest.mark.parametrize('suite', ['all', 'regression', 'known-issues'])
def test_only_known_issue_group_can_be_empty(tmp_path, monkeypatch, suite) -> None:
    report = tmp_path / 'result.xml'
    monkeypatch.setattr(
        sys, 'argv', ['run_readiness', '--suite', suite, '--junitxml', str(report)]
    )

    def run(command, **kwargs):
        report.write_text('<testsuites><testsuite tests="0"/></testsuites>')
        return subprocess.CompletedProcess(command, 5)

    monkeypatch.setattr(run_readiness.subprocess, 'run', run)
    assert run_readiness.main() == (0 if suite == 'known-issues' else 5)


def test_missing_report_cannot_pass(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        sys, 'argv', ['run_readiness', '--junitxml', str(tmp_path / 'missing.xml')]
    )
    monkeypatch.setattr(
        run_readiness.subprocess,
        'run',
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )
    assert run_readiness.main() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'team_spend,member_spend', [(1.0, 2.0), (2.0, 1.0), (2.0, None)]
)
async def test_spend_wait_requires_team_and_member_accounting(team_spend, member_spend):
    user_id = uuid4()
    partial = {
        'team_spend': team_spend,
        'members': {str(user_id): {'spend': member_spend}},
    }
    settled = {'team_spend': 2.0, 'members': {str(user_id): {'spend': 2.0}}}
    adapter = MagicMock(spec=BudgetTestAdapter)
    adapter.financial_data = AsyncMock(side_effect=[partial, settled])

    result = await BudgetTestAdapter.wait_for_spend(
        adapter, 2.0, expected_member_spend={user_id: 2.0}
    )

    assert result == settled
    assert adapter.financial_data.await_count == 2
