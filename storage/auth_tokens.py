from sqlalchemy import BigInteger, CheckConstraint, Identity, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class AuthTokens(Base):
    __tablename__ = 'auth_tokens'

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    keycloak_user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    identity_provider: Mapped[str] = mapped_column(String, nullable=False)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    credential_kind: Mapped[str] = mapped_column(
        String, nullable=False, default='oauth', server_default='oauth'
    )
    provider_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_host: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token_expires_at: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # Time since epoch in seconds
    refresh_token_expires_at: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # Time since epoch in seconds

    __table_args__ = (
        CheckConstraint(
            "credential_kind IN ('manual', 'oauth')", name='ck_auth_tokens_kind'
        ),
        CheckConstraint(
            'provider_account_id IS NULL OR provider_host IS NOT NULL',
            name='ck_auth_tokens_account_host',
        ),
        CheckConstraint(
            "credential_kind != 'manual' OR (provider_account_id IS NOT NULL "
            'AND provider_host IS NOT NULL AND refresh_token IS NULL '
            'AND access_token_expires_at IS NULL AND refresh_token_expires_at IS NULL)',
            name='ck_auth_tokens_manual_credential',
        ),
        Index(
            'idx_auth_tokens_provider_account',
            'identity_provider',
            'provider_host',
            'provider_account_id',
            unique=True,
        ),
        Index(
            'idx_auth_tokens_keycloak_user_identity_provider',
            'keycloak_user_id',
            'identity_provider',
            unique=True,
        ),
    )
