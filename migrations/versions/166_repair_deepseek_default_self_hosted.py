"""Repair the deepseek default seeded onto self-hosted deployments.

Revision ID: 166
Revises: 165
Create Date: 2026-09-01 00:00:00.000000

Migrations 158 and 160 seeded the managed ``deepseek-v4-flash`` verified_models
row (``is_default = is_enabled = is_free = is_verified = true``) on every host,
including self-hosted, where no managed LiteLLM proxy serves the model. PR #476
gated the seed so fresh self-hosted installs no longer get it; this migration
repairs hosts that already ran 158/160 before that gate landed.

On a self-hosted host (``WEB_HOST`` not a managed SaaS host) the migration:

1. Deletes the seeded ``openhands/deepseek-v4-flash`` verified_models row, so
   the model no longer surfaces as default / free / enabled / verified. This
   converges an upgraded self-hosted DB to the same state as a fresh install
   (which, post #476, never seeds the row). SaaS hosts skip the migration
   entirely — the managed default is correct there.
2. Restores org-level ``Default`` LLM profiles that the runtime materializer
   baked onto ``org.llm_profiles`` via ``activate_profile`` while the bogus
   default was live. Most orgs self-heal once the row is gone (the materializer
   strips any ``openhands/``-model ``Default`` at read time when no DB default
   exists); only orgs whose concrete BYOK ``Default`` was overwritten — and
   whose original config survives only in ``org.agent_settings.llm`` — need an
   active restore.

``org.agent_settings`` is plain JSON; ``org.llm_profiles`` is encrypted
(``EncryptedJSON``), so the restore decrypts/encrypts inline like migration 137.

"""

import json
import os
import re
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = '166'
down_revision: str | None = '165'
branch_labels: str | None = None
depends_on: str | None = None

_OPENHANDS_PROVIDER = 'openhands'
_DEFAULT_MODEL = 'deepseek-v4-flash'
# The runtime materializer points the logical ``Default`` profile at this.
_DEFAULT_PROFILE_NAME = 'Default'
_MANAGED_DEFAULT = f'{_OPENHANDS_PROVIDER}/{_DEFAULT_MODEL}'

SAAS_WEB_HOSTS = frozenset(
    {'app.all-hands.dev', 'staging.all-hands.dev', 'dev.all-hands.dev'}
)
SAAS_PREVIEW_HOST = re.compile(r'[a-z0-9][a-z0-9-]*\.staging\.all-hands\.dev')


def _is_self_hosted() -> bool:
    # Unlike server.constants.WEB_HOST, an unset value must read as self-hosted
    # (the repair target) — a bare OHE install leaves WEB_HOST unset.
    host = os.environ.get('WEB_HOST', '').strip()
    return host not in SAAS_WEB_HOSTS and not SAAS_PREVIEW_HOST.fullmatch(host)


def _is_openhands_model(model: str | None) -> bool:
    return bool(model and model.startswith(f'{_OPENHANDS_PROVIDER}/'))


def _decrypt_profiles(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        # Already decrypted by the ORM bind (EncryptedJSON). Treat as plaintext.
        return raw
    if isinstance(raw, str):
        from storage.encrypt_utils import decrypt_value

        return json.loads(decrypt_value(raw))
    return None


def _encrypt_profiles(value: dict[str, Any]) -> str:
    from storage.encrypt_utils import encrypt_value

    return encrypt_value(json.dumps(value))


def _profiles_payload(profiles: dict[str, Any] | None) -> dict[str, Any]:
    return profiles or {'profiles': {}, 'active': None}


def _default_model(profiles: dict[str, Any]) -> str | None:
    default = (profiles.get('profiles') or {}).get(_DEFAULT_PROFILE_NAME)
    if isinstance(default, dict):
        return default.get('model')
    return None


def _legacy_llm_model(agent_settings: dict[str, Any] | None) -> str | None:
    llm = (agent_settings or {}).get('llm')
    if isinstance(llm, dict):
        return llm.get('model')
    return None


def _classify(profiles: dict[str, Any], legacy_model: str | None) -> str:
    """Bucket an org for restore.

    - ``noop``: no stored Default, or a concrete (non-deepseek) Default whose
      stored bytes are intact. Self-heals once the verified_models row is gone.
    - ``restore``: stored Default is the bogus managed model AND a concrete
      BYOK model survives in ``agent_settings.llm`` — restore the Default from
      it.
    - ``strip``: stored Default is the bogus managed model AND there is no
      concrete legacy model — the Default is a phantom baked by activate; drop
      it.
    - ``review``: stored Default is the bogus managed model AND the legacy
      model is itself the managed model — cannot distinguish intent from
      corruption; leave it for manual review (the row delete still strips it
      at read time).
    """
    default_model = _default_model(profiles)
    if default_model != _MANAGED_DEFAULT:
        return 'noop'
    if _is_openhands_model(legacy_model):
        return 'review'
    if legacy_model:
        return 'restore'
    return 'strip'


def _restore_default(
    profiles: dict[str, Any], legacy_llm: dict[str, Any] | None
) -> dict[str, Any]:
    """Rebuild the ``Default`` profile from the surviving legacy LLM."""
    legacy = legacy_llm or {}
    profiles.setdefault('profiles', {})[_DEFAULT_PROFILE_NAME] = {
        'model': legacy.get('model'),
        'base_url': legacy.get('base_url'),
        'api_key': legacy.get('api_key'),
    }
    if profiles.get('active') is None:
        profiles['active'] = _DEFAULT_PROFILE_NAME
    return profiles


def _strip_phantom_default(profiles: dict[str, Any]) -> dict[str, Any]:
    profiles.setdefault('profiles', {}).pop(_DEFAULT_PROFILE_NAME, None)
    if profiles.get('active') == _DEFAULT_PROFILE_NAME:
        profiles['active'] = None
    return profiles


def upgrade() -> None:
    if not _is_self_hosted():
        return

    bind = op.get_bind()

    # Step 1: remove the managed deepseek verified_models row entirely.
    verified_models = sa.table(
        'verified_models',
        sa.column('id', sa.Integer()),
        sa.column('model_name', sa.String()),
        sa.column('provider', sa.String()),
    )
    bind.execute(
        verified_models.delete().where(
            verified_models.c.provider == _OPENHANDS_PROVIDER,
            verified_models.c.model_name == _DEFAULT_MODEL,
        )
    )

    # Step 2: restore org Default profiles baked by the bogus default.
    org = sa.table(
        'org',
        sa.column('id', sa.Uuid()),
        sa.column('agent_settings', sa.JSON()),
        sa.column('llm_profiles', sa.JSON()),
    )
    rows = bind.execute(
        sa.select(org.c.id, org.c.agent_settings, org.c.llm_profiles)
    ).mappings()

    for row in rows:
        raw_profiles = row['llm_profiles']
        profiles = _decrypt_profiles(raw_profiles) or {}
        agent_settings = row['agent_settings']
        if not isinstance(agent_settings, dict):
            agent_settings = {}
        bucket = _classify(profiles, _legacy_llm_model(agent_settings))

        if bucket == 'restore':
            legacy_llm = agent_settings.get('llm')
            legacy_llm = legacy_llm if isinstance(legacy_llm, dict) else {}
            payload = _restore_default(_profiles_payload(profiles), legacy_llm)
        elif bucket == 'strip':
            payload = _strip_phantom_default(_profiles_payload(profiles))
        else:
            # ``noop``: no bogus Default to repair. ``review``: the bogus
            # Default's legacy model is itself managed, so intent is
            # indistinguishable from corruption — leave stored bytes; the
            # verified_models delete already makes the materializer strip this
            # at read time.
            continue

        bind.execute(
            org.update()
            .where(org.c.id == row['id'])
            .values(llm_profiles=_encrypt_profiles(payload))
        )


def downgrade() -> None:
    # Re-applying the bug (re-seeding the managed default onto self-hosted) is
    # not a safe or desirable restore. The forward repair is one-way: SaaS
    # hosts are untouched, and self-hosted hosts converge to fresh-install state.
    pass
