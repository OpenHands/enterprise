"""Regression tests for the budget maintenance CronJob entrypoint."""

import ast
import pathlib


def _budget_maintenance_tree() -> ast.Module:
    source = pathlib.Path(__file__).parent.parent.parent / 'run_budget_maintenance.py'
    return ast.parse(source.read_text())


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
