from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '158_add_free_and_default_to_verified_models.py'
)
spec = spec_from_file_location('migration_158', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_158 = module_from_spec(spec)
spec.loader.exec_module(migration_158)


class _FakeOp:
    """Stand-in for ``alembic.op`` outside a real migration context.

    DDL (``add_column`` / ``create_index``) is a no-op; ``execute`` captures
    the rendered SQL so the seed statements can be inspected.
    """

    def __init__(self) -> None:
        self.statements: list[str] = []

    def add_column(self, *args, **kwargs) -> None:
        pass

    def create_index(self, *args, **kwargs) -> None:
        pass

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


@pytest.fixture
def fake_op(monkeypatch):
    op = _FakeOp()
    monkeypatch.setattr(migration_158, 'op', op)
    return op


def test_upgrade_seeds_deepseek_default_on_saas(monkeypatch, fake_op):
    monkeypatch.setenv('WEB_HOST', 'app.all-hands.dev')

    migration_158.upgrade()

    rendered = '\n'.join(fake_op.statements)
    # Two seed statements: the free-model insert and the default upsert.
    assert rendered.count('INSERT INTO verified_models') == 2
    assert 'is_default = true' in rendered
    assert 'is_free = true' in rendered


@pytest.mark.parametrize(
    'web_host',
    [
        'app.all-hands.dev',
        'staging.all-hands.dev',
        'dev.all-hands.dev',
        'pr-1.staging.all-hands.dev',
        'my-feature-branch.staging.all-hands.dev',
    ],
)
def test_upgrade_seeds_on_managed_deployments(monkeypatch, fake_op, web_host):
    monkeypatch.setenv('WEB_HOST', web_host)
    migration_158.upgrade()
    assert any('INSERT INTO verified_models' in s for s in fake_op.statements)


@pytest.mark.parametrize(
    'web_host',
    [
        '',
        'openhands.example.com',
        'app.all-hands.dev.attacker.com',
        'evil-app.all-hands.dev',
        'app.openhands.ai',
        'a.b.staging.all-hands.dev',
        'staging.all-hands.dev.attacker.com',
    ],
)
def test_upgrade_skips_seed_on_other_deployments(monkeypatch, fake_op, web_host):
    monkeypatch.setenv('WEB_HOST', web_host)
    migration_158.upgrade()
    assert not any('INSERT INTO verified_models' in s for s in fake_op.statements)
