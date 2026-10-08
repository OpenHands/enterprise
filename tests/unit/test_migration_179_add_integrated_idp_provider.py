from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests import postgres_testdb

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '179_add_integrated_idp_provider.py'
)
spec = spec_from_file_location('migration_179', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_179 = module_from_spec(spec)
spec.loader.exec_module(migration_179)


def _clear_env(monkeypatch):
    for key in ('ENABLE_INTEGRATED_IDP', 'OH_DEPLOYMENT_MODE', 'WEB_HOST'):
        monkeypatch.delenv(key, raising=False)


# ── _is_integrated_idp_enabled ──────────────────────────────────────────────


def test_enabled_accepts_true(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('ENABLE_INTEGRATED_IDP', 'true')
    assert migration_179._is_integrated_idp_enabled() is True


def test_enabled_accepts_1(monkeypatch):
    """Older Helm charts default to '1' rather than 'true'."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('ENABLE_INTEGRATED_IDP', '1')
    assert migration_179._is_integrated_idp_enabled() is True


def test_enabled_case_insensitive(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('ENABLE_INTEGRATED_IDP', 'TRUE')
    assert migration_179._is_integrated_idp_enabled() is True


def test_disabled_without_env_var(monkeypatch):
    _clear_env(monkeypatch)
    assert migration_179._is_integrated_idp_enabled() is False


def test_disabled_with_falsy_value(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('ENABLE_INTEGRATED_IDP', 'false')
    assert migration_179._is_integrated_idp_enabled() is False


# ── _is_cloud_deployment ─────────────────────────────────────────────────


def test_cloud_via_deployment_mode(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('OH_DEPLOYMENT_MODE', 'cloud')
    assert migration_179._is_cloud_deployment() is True


def test_self_hosted_via_deployment_mode(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('OH_DEPLOYMENT_MODE', 'self_hosted')
    assert migration_179._is_cloud_deployment() is False


def test_cloud_via_default_web_host(monkeypatch):
    """No ``WEB_HOST`` set mirrors ``server.constants.HOST``'s own default
    (``app.all-hands.dev``) -- unset means cloud."""
    _clear_env(monkeypatch)
    assert migration_179._is_cloud_deployment() is True


def test_cloud_via_all_hands_dev_subdomain(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('WEB_HOST', 'staging.all-hands.dev')
    assert migration_179._is_cloud_deployment() is True


def test_self_hosted_via_custom_web_host(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv('WEB_HOST', 'openhands.customer.example.com')
    assert migration_179._is_cloud_deployment() is False


# ── downgrade is a straightforward DELETE ────────────────────────────────


def test_downgrade_deletes_by_category():
    class _Bind:
        def __init__(self):
            self.statements = []

        def execute(self, statement, params=None):
            self.statements.append((' '.join(str(statement).split()), params))

    bind = _Bind()
    import unittest.mock as mock

    with mock.patch.object(migration_179.op, 'get_bind', return_value=bind):
        migration_179.downgrade()

    assert len(bind.statements) == 1
    sql, params = bind.statements[0]
    assert 'DELETE FROM oauth_providers' in sql
    assert params == {'category': 'integrated_idp'}


# ── real-DB upgrade behavior ─────────────────────────────────────────────


def _provider_rows(engine: Engine) -> list[tuple]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                'SELECT provider_category, display_name, is_idp, client_id, '
                'client_secret, authorization_url, token_url, userinfo_url '
                'FROM oauth_providers WHERE provider_category = :category'
            ),
            {'category': 'integrated_idp'},
        )
        return [tuple(row) for row in rows]


def test_upgrade_seeds_row_when_enabled(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '178'
    )

    monkeypatch.setenv('ENABLE_INTEGRATED_IDP', 'true')
    monkeypatch.setenv('OH_DEPLOYMENT_MODE', 'self_hosted')
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )

    rows = _provider_rows(engine)
    assert len(rows) == 1
    category, display_name, is_idp, client_id, secret, auth_url, token_url, userinfo = (
        rows[0]
    )
    assert category == 'integrated_idp'
    assert is_idp is True
    assert client_id == 'integrated-idp'
    assert secret is None
    assert auth_url is None
    assert token_url is None
    assert userinfo is None


def test_upgrade_noop_when_disabled(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '178'
    )

    monkeypatch.delenv('ENABLE_INTEGRATED_IDP', raising=False)
    monkeypatch.setenv('OH_DEPLOYMENT_MODE', 'self_hosted')
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )

    assert _provider_rows(engine) == []


def test_upgrade_noop_on_cloud_even_when_enabled(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    """The integrated IDP must never be seeded on the managed cloud
    deployment, even if the env var were accidentally set there."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '178'
    )

    monkeypatch.setenv('ENABLE_INTEGRATED_IDP', 'true')
    monkeypatch.setenv('OH_DEPLOYMENT_MODE', 'cloud')
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )

    assert _provider_rows(engine) == []


def test_upgrade_idempotent_when_row_already_exists(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    """Re-running the migration (e.g. a downgrade/upgrade cycle on a host
    that already has the row from a previous upgrade) must not duplicate it."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '178'
    )

    monkeypatch.setenv('ENABLE_INTEGRATED_IDP', 'true')
    monkeypatch.setenv('OH_DEPLOYMENT_MODE', 'self_hosted')
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    assert len(_provider_rows(engine)) == 1

    # Re-running upgrade() directly (bypassing alembic's own
    # already-at-head guard) against the same row must still be a no-op.
    bind = engine.connect()
    try:
        monkeypatch.setattr(migration_179, 'op', SimpleNamespace(get_bind=lambda: bind))
        migration_179.upgrade()
    finally:
        bind.close()

    assert len(_provider_rows(engine)) == 1


def test_downgrade_removes_only_integrated_idp_row(
    monkeypatch, engine: Engine, test_database: postgres_testdb.TestDatabase
):
    """Downgrade must not touch unrelated provider rows."""
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '178'
    )

    monkeypatch.setenv('ENABLE_INTEGRATED_IDP', 'true')
    monkeypatch.setenv('OH_DEPLOYMENT_MODE', 'self_hosted')
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO oauth_providers
                    (provider_category, display_name, is_idp, client_id,
                     permitted_drift_seconds)
                VALUES
                    ('github', 'GitHub', false, 'some-client-id', 60)
                """
            )
        )

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '178'
    )

    assert _provider_rows(engine) == []
    with engine.connect() as conn:
        remaining = (
            conn.execute(text('SELECT provider_category FROM oauth_providers'))
            .scalars()
            .all()
        )
    assert remaining == ['github']
