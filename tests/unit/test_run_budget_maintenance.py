"""Regression tests for the budget maintenance CronJob entrypoint."""

import ast
import pathlib


def _budget_maintenance_tree() -> ast.Module:
    source = (
        pathlib.Path(__file__).parent.parent.parent
        / 'enterprise'
        / 'run_budget_maintenance.py'
    )
    return ast.parse(source.read_text())


def test_run_budget_maintenance_does_not_import_enterprise_package() -> None:
    tree = _budget_maintenance_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and not node.module.startswith('_'):
                assert node.module != 'enterprise', (
                    'run_budget_maintenance.py must not "from enterprise import ..."; '
                    'use an unqualified "import run_maintenance_tasks" instead '
                    '(the Docker image flattens the enterprise/ source tree, so there '
                    'is no top-level "enterprise" package in the container)'
                )


def test_run_budget_maintenance_sets_immediate_task_delay() -> None:
    tree = _budget_maintenance_tree()
    maintenance_task_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'MaintenanceTask'
    ]

    assert maintenance_task_calls, 'run_budget_maintenance.py should enqueue tasks'
    assert any(
        keyword.arg == 'delay'
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == 0
        for call in maintenance_task_calls
        for keyword in call.keywords
    ), (
        'Budget maintenance tasks must set delay=0 for deployed DB schemas without '
        'a delay default'
    )
