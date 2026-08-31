"""Backfill OpenHands free/default verified model row.

Revision ID: 156
Revises: 155
Create Date: 2026-08-31 00:00:00.000000

"""

import json
import os
import re
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '156'
down_revision: Union[str, None] = '155'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FREE_MODELS = ('deepseek-v4-flash',)
_DEFAULT_MODEL = 'deepseek-v4-flash'

SAAS_WEB_HOSTS = frozenset(
    {
        'app.all-hands.dev',
        'staging.all-hands.dev',
        'dev.all-hands.dev',
    }
)
SAAS_PREVIEW_HOST = re.compile(r'[a-z0-9][a-z0-9-]*\.staging\.all-hands\.dev')


def _is_saas_web_host(host: str) -> bool:
    return host in SAAS_WEB_HOSTS or bool(SAAS_PREVIEW_HOST.fullmatch(host))


JSON_MODEL_COLUMNS = (
    ('user_settings', 'id', 'agent_settings'),
    ('org', 'id', 'agent_settings'),
)

ENCRYPTED_LLM_PROFILE_COLUMNS = (
    ('user', 'id', 'llm_profiles'),
    ('org', 'id', 'llm_profiles'),
)


def _quote_table_name(table_name: str) -> str:
    return f'"{table_name}"' if table_name == 'user' else table_name


def _replace_model_values(value: Any, replacements: dict[str, str]) -> tuple[Any, bool]:
    if isinstance(value, dict):
        changed = False
        replaced = {}
        for key, item in value.items():
            if key == 'model' and isinstance(item, str) and item in replacements:
                replaced[key] = replacements[item]
                changed = True
                continue

            new_item, item_changed = _replace_model_values(item, replacements)
            replaced[key] = new_item
            changed = changed or item_changed
        return replaced, changed

    if isinstance(value, list):
        changed = False
        replaced_items = []
        for item in value:
            new_item, item_changed = _replace_model_values(item, replacements)
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


def _update_json_column(
    bind,
    table_name: str,
    id_column_name: str,
    column_name: str,
    replacements: dict[str, str],
) -> int:
    quoted_table_name = _quote_table_name(table_name)
    rows = bind.execute(
        sa.text(
            f"""
            SELECT {id_column_name}, {column_name}
            FROM {quoted_table_name}
            WHERE {column_name} IS NOT NULL
            """
        )
    ).mappings()

    updated = 0
    for row in rows:
        current = row[column_name]
        if not isinstance(current, dict):
            continue
        replaced, changed = _replace_model_values(current, replacements)
        if not changed:
            continue
        bind.execute(
            sa.text(
                f"""
                UPDATE {quoted_table_name}
                SET {column_name} = CAST(:payload AS json)
                WHERE {id_column_name} = :row_id
                """
            ),
            {'payload': json.dumps(replaced), 'row_id': row[id_column_name]},
        )
        updated += 1

    return updated


def _update_encrypted_json_column(
    bind,
    table_name: str,
    id_column_name: str,
    column_name: str,
    replacements: dict[str, str],
) -> int:
    quoted_table_name = _quote_table_name(table_name)
    rows = bind.execute(
        sa.text(
            f"""
            SELECT {id_column_name}, {column_name}
            FROM {quoted_table_name}
            WHERE {column_name} IS NOT NULL
            """
        )
    ).mappings()

    updated = 0
    for row in rows:
        current = _decrypt_json(row[column_name])
        replaced, changed = _replace_model_values(current, replacements)
        if not changed:
            continue
        bind.execute(
            sa.text(
                f"""
                UPDATE {quoted_table_name}
                SET {column_name} = :payload
                WHERE {id_column_name} = :row_id
                """
            ),
            {'payload': _encrypt_json(replaced), 'row_id': row[id_column_name]},
        )
        updated += 1

    return updated


def _update_org_member_agent_settings_diff(replacements: dict[str, str]) -> None:
    for old_model, new_model in replacements.items():
        op.execute(
            sa.text(
                """
                UPDATE org_member
                SET agent_settings_diff = jsonb_set(
                    agent_settings_diff::jsonb,
                    '{llm,model}',
                    to_jsonb(CAST(:new_model AS text)),
                    false
                )::json
                WHERE agent_settings_diff::jsonb #>> '{llm,model}' = :old_model
                """
            ).bindparams(old_model=old_model, new_model=new_model)
        )


def _current_openhands_default_model_name(bind) -> str:
    current = bind.execute(
        sa.text(
            """
            SELECT model_name
            FROM verified_models
            WHERE provider = 'openhands'
              AND is_default = true
            LIMIT 1
            """
        )
    ).scalar()
    return current or _DEFAULT_MODEL


def _rewrite_previous_platform_default_references(bind) -> None:
    web_host = os.environ.get('WEB_HOST', '').strip()
    if not _is_saas_web_host(web_host):
        print(
            f'Skipping stale default reference rewrite: WEB_HOST {web_host!r} '
            'is not an All-Hands-managed deployment.'
        )
        return

    new_model_name = _current_openhands_default_model_name(bind)
    if new_model_name == _DEFAULT_MODEL:
        return

    new_model = f'openhands/{new_model_name}'
    replacements = {
        f'openhands/{_DEFAULT_MODEL}': new_model,
        f'litellm_proxy/{_DEFAULT_MODEL}': new_model,
    }

    for table_name, id_column_name, column_name in JSON_MODEL_COLUMNS:
        _update_json_column(bind, table_name, id_column_name, column_name, replacements)

    _update_org_member_agent_settings_diff(replacements)

    for table_name, id_column_name, column_name in ENCRYPTED_LLM_PROFILE_COLUMNS:
        _update_encrypted_json_column(
            bind, table_name, id_column_name, column_name, replacements
        )


def upgrade() -> None:
    for model_name in _FREE_MODELS:
        op.execute(
            sa.text(
                """
                INSERT INTO verified_models (
                    model_name,
                    provider,
                    is_enabled,
                    is_verified,
                    is_free,
                    is_default
                )
                VALUES (:model_name, 'openhands', true, true, true, false)
                ON CONFLICT (model_name, provider) DO UPDATE
                SET is_enabled = true,
                    is_verified = true,
                    is_free = true,
                    updated_at = CURRENT_TIMESTAMP
                """
            ).bindparams(model_name=model_name)
        )

    op.execute(
        sa.text(
            """
            UPDATE verified_models
            SET is_default = true,
                is_enabled = true,
                is_verified = true,
                is_free = true,
                updated_at = CURRENT_TIMESTAMP
            WHERE model_name = :model_name
              AND provider = 'openhands'
              AND NOT EXISTS (
                  SELECT 1
                  FROM verified_models
                  WHERE provider = 'openhands'
                    AND is_default = true
              )
            """
        ).bindparams(model_name=_DEFAULT_MODEL)
    )

    _rewrite_previous_platform_default_references(op.get_bind())


def downgrade() -> None:
    for model_name in _FREE_MODELS:
        op.execute(
            sa.text(
                """
                UPDATE verified_models
                SET is_free = false,
                    updated_at = CURRENT_TIMESTAMP
                WHERE model_name = :model_name
                  AND provider = 'openhands'
                """
            ).bindparams(model_name=model_name)
        )

    op.execute(
        sa.text(
            """
            UPDATE verified_models
            SET is_default = false,
                updated_at = CURRENT_TIMESTAMP
            WHERE model_name = :model_name
              AND provider = 'openhands'
            """
        ).bindparams(model_name=_DEFAULT_MODEL)
    )
