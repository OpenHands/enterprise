"""Migrate OpenHands MiniMax M2.7 settings to GLM 5.2."""

import json
import os
import re
from typing import Any, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '143'
down_revision: str | None = '142'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Only prefixed values are rewritten. A bare ``minimax-m2.7`` is not written by
# the managed default path, so such rows are BYOK configs pointed at a
# third-party base_url; rewriting them would aim a MiniMax key at a GLM model.
MODEL_REPLACEMENTS = {
    'openhands/minimax-m2.7': 'openhands/glm-5.2',
    'litellm_proxy/minimax-m2.7': 'litellm_proxy/glm-5.2',
}

# Hosts of the All-Hands-managed deployments. The rewrite is only correct where
# the managed LiteLLM proxy actually serves ``glm-5.2``; a self-hosted install
# runs the same image and chart against its own proxy, and neither the model
# string nor ``base_url`` distinguishes it (``LITE_LLM_API_URL`` defaults to the
# managed URL). ``WEB_HOST`` is the one value that differs per deployment.
SAAS_WEB_HOSTS = frozenset(
    {
        'app.all-hands.dev',
        'staging.all-hands.dev',
        'dev.all-hands.dev',
    }
)

# Preview deployments render WEB_HOST as ``{branchSanitized}.{ingress.host}``, so
# they are always a single extra label under staging. ``branchSanitized`` is a
# generic host prefix (the feature ApplicationSet passes ``pr-<number>``, but the
# name and the chart both allow any sanitized branch), so match the whole label.
SAAS_PREVIEW_HOST = re.compile(r'[a-z0-9][a-z0-9-]*\.staging\.all-hands\.dev')


def _is_saas_web_host(host: str) -> bool:
    return host in SAAS_WEB_HOSTS or bool(SAAS_PREVIEW_HOST.fullmatch(host))


JSON_MODEL_COLUMNS = (
    ('user_settings', 'agent_settings'),
    ('org', 'agent_settings'),
    ('org_member', 'agent_settings_diff'),
)

ENCRYPTED_LLM_PROFILE_COLUMNS = (
    ('user', 'id', 'llm_profiles'),
    ('org', 'id', 'llm_profiles'),
)


def _replace_llm_model_statement(table_name: str, column_name: str) -> sa.TextClause:
    return sa.text(
        f"""
        UPDATE {table_name}
        SET {column_name} = jsonb_set(
            {column_name}::jsonb,
            '{{llm,model}}',
            to_jsonb(CAST(:new_model AS text)),
            false
        )::json
        WHERE {column_name}::jsonb #>> '{{llm,model}}' = :old_model
        """
    )


def _replace_model_values(value: Any) -> tuple[Any, bool]:
    if isinstance(value, dict):
        changed = False
        replaced = {}
        for key, item in value.items():
            if key == 'model' and isinstance(item, str) and item in MODEL_REPLACEMENTS:
                replaced[key] = MODEL_REPLACEMENTS[item]
                changed = True
                continue

            new_item, item_changed = _replace_model_values(item)
            replaced[key] = new_item
            changed = changed or item_changed
        return replaced, changed

    if isinstance(value, list):
        changed = False
        replaced_items = []
        for item in value:
            new_item, item_changed = _replace_model_values(item)
            replaced_items.append(new_item)
            changed = changed or item_changed
        return replaced_items, changed

    return value, False


def _encrypt_json(value: dict[str, Any]) -> str:
    from storage.encrypt_utils import encrypt_value

    return encrypt_value(json.dumps(value))


def _decrypt_json(value: str) -> dict[str, Any]:
    from storage.encrypt_utils import decrypt_value

    decrypted = json.loads(decrypt_value(value))
    if not isinstance(decrypted, dict):
        raise ValueError('Expected encrypted LLM profiles to be a JSON object')
    return decrypted


def _upgrade_encrypted_llm_profile_column(
    bind,
    table_name: str,
    id_column_name: str,
    profile_column_name: str,
) -> int:
    quoted_table_name = f'"{table_name}"' if table_name == 'user' else table_name
    rows = bind.execute(
        sa.text(
            f"""
            SELECT {id_column_name}, {profile_column_name}
            FROM {quoted_table_name}
            WHERE {profile_column_name} IS NOT NULL
            """
        )
    ).mappings()

    updated = 0
    for row in rows:
        decrypted = _decrypt_json(row[profile_column_name])
        replaced, changed = _replace_model_values(decrypted)
        if not changed:
            continue
        bind.execute(
            sa.text(
                f"""
                UPDATE {quoted_table_name}
                SET {profile_column_name} = :encrypted_profiles
                WHERE {id_column_name} = :row_id
                """
            ),
            {
                'encrypted_profiles': _encrypt_json(replaced),
                'row_id': row[id_column_name],
            },
        )
        updated += 1

    return updated


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    # Unlike server.constants.WEB_HOST this does not default to a managed host:
    # an unset value must not be read as "this is SaaS".
    web_host = os.environ.get('WEB_HOST', '').strip()
    if not _is_saas_web_host(web_host):
        print(
            f'Skipping migration 143: WEB_HOST {web_host!r} is not an '
            'All-Hands-managed deployment.'
        )
        return

    for table_name, column_name in JSON_MODEL_COLUMNS:
        statement = _replace_llm_model_statement(table_name, column_name)
        for old_model, new_model in MODEL_REPLACEMENTS.items():
            bind.execute(
                statement,
                {'old_model': old_model, 'new_model': new_model},
            )

    for (
        table_name,
        id_column_name,
        profile_column_name,
    ) in ENCRYPTED_LLM_PROFILE_COLUMNS:
        _upgrade_encrypted_llm_profile_column(
            bind,
            table_name,
            id_column_name,
            profile_column_name,
        )


def downgrade() -> None:
    pass
