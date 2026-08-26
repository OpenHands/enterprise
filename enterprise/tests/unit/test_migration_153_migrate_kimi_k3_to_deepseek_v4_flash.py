from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "153_migrate_kimi_k3_to_deepseek_v4_flash.py"
)
spec = spec_from_file_location("migration_153", MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_153 = module_from_spec(spec)
spec.loader.exec_module(migration_153)


def test_replacement_set_is_exact_and_managed_only():
    assert migration_153.MODEL_REPLACEMENTS == {
        "openhands/kimi-k3": "openhands/deepseek-v4-flash",
        "litellm_proxy/kimi-k3": "litellm_proxy/deepseek-v4-flash",
    }
    assert "anthropic/kimi-k3" not in migration_153.MODEL_REPLACEMENTS
    assert "custom/kimi-k3" not in migration_153.MODEL_REPLACEMENTS
    # A bare name carries no provenance, so it is treated as BYOK, not managed.
    assert "kimi-k3" not in migration_153.MODEL_REPLACEMENTS


def test_statement_updates_nested_llm_model_by_exact_match():
    statement = migration_153._replace_llm_model_statement(
        "org_member",
        "agent_settings_diff",
    )
    sql = " ".join(str(statement).split())

    assert sql.startswith("UPDATE org_member SET agent_settings_diff = jsonb_set(")
    assert "agent_settings_diff::jsonb, '{llm,model}'" in sql
    assert "to_jsonb(CAST(:new_model AS text))" in sql
    assert "WHERE agent_settings_diff::jsonb #>> '{llm,model}' = :old_model" in sql


def test_upgrade_updates_each_settings_column_for_each_replacement(monkeypatch):
    calls = []

    class Result:
        def mappings(self):
            return []

    class Bind:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement, params=None):
            if str(statement).lstrip().startswith("SELECT "):
                return Result()
            calls.append((statement, params))
            return None

    monkeypatch.setenv("WEB_HOST", "app.all-hands.dev")
    monkeypatch.setattr(
        migration_153,
        "op",
        SimpleNamespace(get_bind=lambda: Bind()),
    )

    migration_153.upgrade()

    assert len(calls) == (
        len(migration_153.JSON_MODEL_COLUMNS) * len(migration_153.MODEL_REPLACEMENTS)
    )
    assert [params for _, params in calls[:2]] == [
        {"old_model": "openhands/kimi-k3", "new_model": "openhands/deepseek-v4-flash"},
        {
            "old_model": "litellm_proxy/kimi-k3",
            "new_model": "litellm_proxy/deepseek-v4-flash",
        },
    ]

    updated_tables = [
        " ".join(str(statement).split()).partition(" SET ")[0] for statement, _ in calls
    ]
    assert updated_tables == [
        "UPDATE user_settings",
        "UPDATE user_settings",
        "UPDATE org",
        "UPDATE org",
        "UPDATE org_member",
        "UPDATE org_member",
    ]


def test_replace_model_values_updates_nested_profile_models_only_on_exact_match():
    payload = {
        "profiles": {
            "managed": {"model": "openhands/kimi-k3"},
            "legacy_proxy": {"model": "litellm_proxy/kimi-k3"},
            "bare": {"model": "kimi-k3"},
            "custom": {"model": "anthropic/kimi-k3"},
        },
        "active": "managed",
    }

    replaced, changed = migration_153._replace_model_values(payload)

    assert changed is True
    assert replaced["profiles"]["managed"]["model"] == "openhands/deepseek-v4-flash"
    assert (
        replaced["profiles"]["legacy_proxy"]["model"]
        == "litellm_proxy/deepseek-v4-flash"
    )
    assert replaced["profiles"]["bare"]["model"] == "kimi-k3"
    assert replaced["profiles"]["custom"]["model"] == "anthropic/kimi-k3"
    assert payload["profiles"]["managed"]["model"] == "openhands/kimi-k3"


def test_upgrade_encrypted_llm_profiles_reencrypts_changed_rows(monkeypatch):
    executed_updates = []
    rows = [
        {
            "id": "user-1",
            "llm_profiles": "encrypted-managed",
        },
        {
            "id": "user-2",
            "llm_profiles": "encrypted-custom",
        },
    ]

    class Result:
        def mappings(self):
            return rows

    class Bind:
        def execute(self, statement, params=None):
            if str(statement).lstrip().startswith("SELECT "):
                return Result()
            executed_updates.append((statement, params))
            return None

    def fake_decrypt(value):
        if value == "encrypted-managed":
            return {
                "profiles": {
                    "Default": {"model": "openhands/kimi-k3"},
                },
                "active": "Default",
            }
        return {
            "profiles": {
                "Custom": {"model": "anthropic/kimi-k3"},
            },
            "active": "Custom",
        }

    encrypted_payloads = []

    def fake_encrypt(value):
        encrypted_payloads.append(value)
        return "reencrypted"

    monkeypatch.setattr(migration_153, "_decrypt_json", fake_decrypt)
    monkeypatch.setattr(migration_153, "_encrypt_json", fake_encrypt)

    updated = migration_153._upgrade_encrypted_llm_profile_column(
        Bind(),
        "user",
        "id",
        "llm_profiles",
    )

    assert updated == 1
    assert len(executed_updates) == 1
    sql = " ".join(str(executed_updates[0][0]).split())
    assert sql.startswith('UPDATE "user" SET llm_profiles = :encrypted_profiles')
    assert executed_updates[0][1] == {
        "encrypted_profiles": "reencrypted",
        "row_id": "user-1",
    }
    assert encrypted_payloads == [
        {
            "profiles": {
                "Default": {"model": "openhands/deepseek-v4-flash"},
            },
            "active": "Default",
        }
    ]


def test_upgrade_rejects_non_postgresql(monkeypatch):
    bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    monkeypatch.setattr(
        migration_153,
        "op",
        SimpleNamespace(get_bind=lambda: bind),
    )

    with pytest.raises(RuntimeError, match="Unsupported database dialect: sqlite"):
        migration_153.upgrade()


@pytest.mark.parametrize(
    "web_host",
    [
        "app.all-hands.dev",
        "staging.all-hands.dev",
        "dev.all-hands.dev",
        "pr-1.staging.all-hands.dev",
        "pr-12345.staging.all-hands.dev",
        # branchSanitized is a generic host prefix, not only pr-<number>.
        "my-feature-branch.staging.all-hands.dev",
    ],
)
def test_is_saas_web_host_accepts_managed_deployments(web_host):
    assert migration_153._is_saas_web_host(web_host)


@pytest.mark.parametrize(
    "web_host",
    [
        "",
        "openhands.example.com",
        "app.all-hands.dev.attacker.com",
        "evil-app.all-hands.dev",
        "app.openhands.ai",
        "pr-1.staging.all-hands.dev.attacker.com",
        # Only one label is delegated; deeper nesting is not a preview host.
        "a.b.staging.all-hands.dev",
        "staging.all-hands.dev.attacker.com",
    ],
)
def test_is_saas_web_host_rejects_other_deployments(web_host):
    assert not migration_153._is_saas_web_host(web_host)


def test_upgrade_skips_when_web_host_is_self_hosted(monkeypatch):
    calls = []

    class Bind:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement, params=None):
            calls.append((statement, params))
            return None

    monkeypatch.setenv("WEB_HOST", "openhands.example.com")
    monkeypatch.setattr(
        migration_153,
        "op",
        SimpleNamespace(get_bind=lambda: Bind()),
    )

    migration_153.upgrade()

    assert calls == []


def test_upgrade_skips_when_web_host_is_unset(monkeypatch):
    calls = []

    class Bind:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, statement, params=None):
            calls.append((statement, params))
            return None

    monkeypatch.delenv("WEB_HOST", raising=False)
    monkeypatch.setattr(
        migration_153,
        "op",
        SimpleNamespace(get_bind=lambda: Bind()),
    )

    migration_153.upgrade()

    assert calls == []
