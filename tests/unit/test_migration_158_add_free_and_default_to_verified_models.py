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


def test_upgrade_adds_columns_and_index_on_self_hosted(monkeypatch, fake_op):
    """Columns and the default-enforcing index ship to every deployment.

    The DDL (add_column / create_index) is issued through ``op``, not
    ``op.execute``, so the seed gate does not affect it. Assert the gate's
    early return leaves only the DDL path and no seed SQL.
    """
    monkeypatch.setenv('WEB_HOST', 'openhands.example.com')

    migration_158.upgrade()

    rendered = '\n'.join(fake_op.statements)
    # No managed-deployment seed statements run on self-hosted.
    assert 'INSERT INTO verified_models' not in rendered
    assert 'is_default = true' not in rendered


def test_upgrade_skips_deepseek_seed_when_web_host_is_unset(monkeypatch, fake_op):
    monkeypatch.delenv('WEB_HOST', raising=False)

    migration_158.upgrade()

    rendered = '\n'.join(fake_op.statements)
    assert 'INSERT INTO verified_models' not in rendered


def test_upgrade_skips_deepseek_seed_when_web_host_is_self_hosted(
    monkeypatch, fake_op
):
    monkeypatch.setenv('WEB_HOST', 'openhands.example.com')

    migration_158.upgrade()

    rendered = '\n'.join(fake_op.statements)
    assert 'INSERT INTO verified_models' not in rendered


def test_upgrade_seeds_deepseek_default_on_saas(monkeypatch, fake_op):
    monkeypatch.setenv('WEB_HOST', 'app.all-hands.dev')

    migration_158.upgrade()

    rendered = '\n'.join(fake_op.statements)
    # Two seed statements: the free-model insert and the default upsert.
    assert rendered.count('INSERT INTO verified_models') == 2
    # The default seed forces is_default=true (unconditional on SaaS).
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
def test_is_saas_accepts_managed_deployments(monkeypatch, web_host):
    monkeypatch.setenv('WEB_HOST', web_host)
    assert migration_158._is_saas()


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
def test_is_saas_rejects_other_deployments(monkeypatch, web_host):
    monkeypatch.setenv('WEB_HOST', web_host)
    assert not migration_158._is_saas()
