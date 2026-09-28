"""Add oauth_providers, oauth_provider_users, oauth_tokens tables and seed providers.

Phase 1 of the OAuth auth refactor (OHE-3293/3294). Purely additive: the new
tables, stores, and routes exist but are not wired into the main login flow.

Three tables:
- ``oauth_providers`` — per-provider OAuth/OIDC config (IDP or git provider).
- ``oauth_provider_users`` — external-identity → internal-user mapping.
- ``oauth_tokens`` — per-user credential pair (denormalized, encrypted).

Seeding: one IDP row for Keycloak (from ``KEYCLOAK_*`` env vars) and one row
per configured git provider (when its ``*_CLIENT_ID`` env var is set).
``client_secret`` is encrypted at rest via the existing JWE service.

Revision ID: 168
Revises: 167
Create Date: 2026-09-23 00:00:00.000000
"""

import json
import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '168'
down_revision: str | None = '167'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ── helpers ────────────────────────────────────────────────────────────────


def _encrypt_secret(plaintext: str) -> str:
    """Encrypt a client secret for the EncryptedJSON column.

    Mirrors ``EncryptedJSON.process_bind_param``: ``encrypt_value(json.dumps(value))``.
    A secret is stored as ``{"v": "<secret>"}`` so it round-trips through the
    ``EncryptedJSON`` TypeDecorator (which JSON-loads on read).
    """
    from storage.encrypt_utils import encrypt_value

    return encrypt_value(json.dumps({'v': plaintext}))


def _seed_row(
    bind,
    provider_category: str,
    display_name: str,
    is_idp: bool,
    client_id: str,
    client_secret: str | None,
    authorization_url: str | None,
    token_url: str | None,
    userinfo_url: str | None,
    scopes: list[str] | None,
) -> None:
    encrypted_secret = _encrypt_secret(client_secret) if client_secret else None
    bind.execute(
        sa.text(
            """
            INSERT INTO oauth_providers
                (provider_category, display_name, is_idp, client_id,
                 client_secret, authorization_url, token_url, userinfo_url,
                 scopes, permitted_drift_seconds, created_at, updated_at)
            VALUES
                (:provider_category, :display_name, :is_idp, :client_id,
                 :client_secret, :authorization_url, :token_url, :userinfo_url,
                 :scopes, 60, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ),
        {
            'provider_category': provider_category,
            'display_name': display_name,
            'is_idp': is_idp,
            'client_id': client_id,
            'client_secret': encrypted_secret,
            'authorization_url': authorization_url,
            'token_url': token_url,
            'userinfo_url': userinfo_url,
            'scopes': json.dumps(scopes) if scopes else None,
        },
    )


def _seed_from_environment() -> None:
    """Seed oauth_providers from environment variables.

    - One IDP row for Keycloak when ``KEYCLOAK_CLIENT_ID`` is set.
    - One git-provider row per configured git provider.
    """
    bind = op.get_bind()

    # Keycloak as IDP
    kc_client_id = os.getenv('KEYCLOAK_CLIENT_ID', '').strip()
    if kc_client_id:
        kc_server_url = os.getenv('KEYCLOAK_SERVER_URL', '').rstrip('/')
        kc_realm = os.getenv('KEYCLOAK_REALM_NAME', '')
        kc_client_secret = os.getenv('KEYCLOAK_CLIENT_SECRET', '').strip()
        kc_base = (
            f'{kc_server_url}/realms/{kc_realm}' if kc_server_url and kc_realm else ''
        )
        _seed_row(
            bind,
            provider_category='enterprise_sso',
            display_name='Keycloak',
            is_idp=True,
            client_id=kc_client_id,
            client_secret=kc_client_secret or None,
            authorization_url=(
                f'{kc_base}/protocol/openid-connect/auth' if kc_base else None
            ),
            token_url=(f'{kc_base}/protocol/openid-connect/token' if kc_base else None),
            userinfo_url=(
                f'{kc_base}/protocol/openid-connect/userinfo' if kc_base else None
            ),
            scopes=['openid', 'email', 'profile'],
        )

    # GitHub git provider
    gh_client_id = os.getenv('GITHUB_APP_CLIENT_ID', '').strip()
    if gh_client_id:
        _seed_row(
            bind,
            provider_category='github',
            display_name='GitHub',
            is_idp=False,
            client_id=gh_client_id,
            client_secret=os.getenv('GITHUB_APP_CLIENT_SECRET', '').strip() or None,
            authorization_url='https://github.com/login/oauth/authorize',
            token_url='https://github.com/login/oauth/access_token',
            userinfo_url='https://api.github.com/user',
            scopes=['repo', 'user:email', 'workflow'],
        )

    # GitLab git provider
    gl_client_id = os.getenv('GITLAB_APP_CLIENT_ID', '').strip()
    if gl_client_id:
        gl_host = os.getenv('GITLAB_HOST', 'gitlab.com')
        _seed_row(
            bind,
            provider_category='gitlab',
            display_name='GitLab',
            is_idp=False,
            client_id=gl_client_id,
            client_secret=os.getenv('GITLAB_APP_CLIENT_SECRET', '').strip() or None,
            authorization_url=f'https://{gl_host}/oauth/authorize',
            token_url=f'https://{gl_host}/oauth/token',
            userinfo_url=f'https://{gl_host}/api/v4/user',
            scopes=['api', 'read_user', 'write_repository'],
        )

    # Bitbucket Cloud git provider
    bb_client_id = os.getenv('BITBUCKET_APP_CLIENT_ID', '').strip()
    if bb_client_id:
        _seed_row(
            bind,
            provider_category='bitbucket',
            display_name='Bitbucket',
            is_idp=False,
            client_id=bb_client_id,
            client_secret=(
                os.getenv('BITBUCKET_APP_CLIENT_SECRET', '').strip() or None
            ),
            authorization_url='https://bitbucket.org/site/oauth2/authorize',
            token_url='https://bitbucket.org/site/oauth2/access_token',
            userinfo_url='https://api.bitbucket.org/2.0/user',
            scopes=['repository', 'account'],
        )

    # Bitbucket Data Center git provider
    bbdc_client_id = os.getenv('BITBUCKET_DATA_CENTER_CLIENT_ID', '').strip()
    if bbdc_client_id:
        bbdc_host = os.getenv('BITBUCKET_DATA_CENTER_HOST', '').strip()
        _seed_row(
            bind,
            provider_category='bitbucket_data_center',
            display_name='Bitbucket Data Center',
            is_idp=False,
            client_id=bbdc_client_id,
            client_secret=(
                os.getenv('BITBUCKET_DATA_CENTER_CLIENT_SECRET', '').strip() or None
            ),
            authorization_url=(
                f'https://{bbdc_host}/rest/oauth2/latest/authorize'
                if bbdc_host
                else None
            ),
            token_url=(
                f'https://{bbdc_host}/rest/oauth2/latest/token' if bbdc_host else None
            ),
            userinfo_url=(
                f'https://{bbdc_host}/rest/api/1.0/users/current' if bbdc_host else None
            ),
            scopes=['PROJECT_READ', 'REPO_READ', 'REPO_WRITE'],
        )

    # Azure DevOps git provider
    az_client_id = os.getenv('AZURE_DEVOPS_CLIENT_ID', '').strip()
    if az_client_id:
        az_tenant = os.getenv('AZURE_DEVOPS_TENANT_ID', '').strip()
        _seed_row(
            bind,
            provider_category='azure_devops',
            display_name='Azure DevOps',
            is_idp=False,
            client_id=az_client_id,
            client_secret=(os.getenv('AZURE_DEVOPS_CLIENT_SECRET', '').strip() or None),
            authorization_url=(
                f'https://login.microsoftonline.com/{az_tenant}/oauth2/v2.0/authorize'
                if az_tenant
                else None
            ),
            token_url=(
                f'https://login.microsoftonline.com/{az_tenant}/oauth2/v2.0/token'
                if az_tenant
                else None
            ),
            userinfo_url=None,
            scopes=['499b84ac-1321-427f-aa17-267ca6975798/.default'],
        )


# ── migration ────────────────────────────────────────────────────���─────────


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    inspector = sa.inspect(bind)
    if not inspector.has_table('oauth_providers'):
        op.create_table(
            'oauth_providers',
            sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
            sa.Column('provider_category', sa.String(64), nullable=False),
            sa.Column('display_name', sa.String(128), nullable=False),
            sa.Column('is_idp', sa.Boolean(), nullable=False),
            sa.Column('client_id', sa.String(255), nullable=False),
            sa.Column('client_secret', sa.String(), nullable=True),
            sa.Column('authorization_url', sa.String(2048), nullable=True),
            sa.Column('token_url', sa.String(2048), nullable=True),
            sa.Column('userinfo_url', sa.String(2048), nullable=True),
            sa.Column('scopes', sa.JSON(), nullable=True),
            sa.Column('permitted_drift_seconds', sa.Integer(), nullable=False),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text('CURRENT_TIMESTAMP'),
            ),
            sa.Column(
                'updated_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text('CURRENT_TIMESTAMP'),
            ),
        )
        op.create_index(
            'ix_oauth_providers_provider_category',
            'oauth_providers',
            ['provider_category'],
        )
        op.create_index('ix_oauth_providers_is_idp', 'oauth_providers', ['is_idp'])

    if not inspector.has_table('oauth_provider_users'):
        op.create_table(
            'oauth_provider_users',
            sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
            sa.Column(
                'oauth_provider_id',
                sa.Integer(),
                sa.ForeignKey('oauth_providers.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column(
                'user_id',
                sa.UUID(),
                sa.ForeignKey('user.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('external_subject_id', sa.String(255), nullable=False),
            sa.Column('external_email', sa.String(255), nullable=True),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text('CURRENT_TIMESTAMP'),
            ),
            sa.Column(
                'updated_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text('CURRENT_TIMESTAMP'),
            ),
            sa.UniqueConstraint(
                'oauth_provider_id',
                'external_subject_id',
                name='uq_oauth_provider_users_provider_subject',
            ),
        )
        op.create_index(
            'ix_oauth_provider_users_user_provider',
            'oauth_provider_users',
            ['user_id', 'oauth_provider_id'],
        )

    if not inspector.has_table('oauth_tokens'):
        op.create_table(
            'oauth_tokens',
            sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
            sa.Column(
                'user_id',
                sa.UUID(),
                sa.ForeignKey('user.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column(
                'oauth_provider_id',
                sa.Integer(),
                sa.ForeignKey('oauth_providers.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('access_token', sa.String(), nullable=False),
            sa.Column('refresh_token', sa.String(), nullable=True),
            sa.Column(
                'access_token_expires_at',
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                'refresh_token_expires_at',
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text('CURRENT_TIMESTAMP'),
            ),
            sa.Column(
                'updated_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text('CURRENT_TIMESTAMP'),
            ),
            sa.UniqueConstraint(
                'user_id',
                'oauth_provider_id',
                name='uq_oauth_tokens_user_provider',
            ),
        )

    _seed_from_environment()


def downgrade() -> None:
    op.drop_table('oauth_tokens')
    op.drop_index(
        'ix_oauth_provider_users_user_provider',
        table_name='oauth_provider_users',
    )
    op.drop_table('oauth_provider_users')
    op.drop_index('ix_oauth_providers_is_idp', table_name='oauth_providers')
    op.drop_index('ix_oauth_providers_provider_category', table_name='oauth_providers')
    op.drop_table('oauth_providers')
