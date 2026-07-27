"""Regression test: run_budget_maintenance.py must not import the 'enterprise' package.

The production Docker image copies the enterprise source tree into WORKDIR /app as
`COPY enterprise .`, which flattens the directory and means there is no importable
top-level 'enterprise' package in the container.  Any such import would cause a
ModuleNotFoundError at cron runtime, silently preventing budget maintenance from ever
running and breaking org/user budget cap enforcement.
"""

import ast
import pathlib


def test_run_budget_maintenance_does_not_import_enterprise_package() -> None:
    source = (
        pathlib.Path(__file__).parent.parent.parent
        / 'enterprise'
        / 'run_budget_maintenance.py'
    )
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and not node.module.startswith('_'):
                assert node.module != 'enterprise', (
                    'run_budget_maintenance.py must not "from enterprise import ..."; '
                    'use an unqualified "import run_maintenance_tasks" instead '
                    '(the Docker image flattens the enterprise/ source tree, so there '
                    'is no top-level "enterprise" package in the container)'
                )
