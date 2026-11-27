"""SQLAlchemy model for Organization."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from openhands.app_server.settings.meta_profiles import OrgMetaProfiles
from openhands.app_server.settings.settings_editor import SettingsOperation
from openhands.app_server.settings.settings_models import SharedSettings
from openhands.sdk.settings import ConversationSettings, validate_agent_settings
from server.constants import DEFAULT_BILLING_MARGIN
from storage.base import Base
from storage.encrypt_utils import EncryptedJSON, decrypt_value, encrypt_value

if TYPE_CHECKING:
    from storage.api_key import ApiKey
    from storage.billing_session import BillingSession
    from storage.org_git_claim import OrgGitClaim
    from storage.org_invitation import OrgInvitation
    from storage.org_member import OrgMember
    from storage.slack_conversation import SlackConversation
    from storage.slack_user import SlackUser
    from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas
    from storage.stored_custom_secrets import StoredCustomSecrets
    from storage.stripe_customer import StripeCustomer
    from storage.user import User


class OrganizationSettings(SharedSettings):
    """Organization preferences, including versioned SDK settings blocks."""

    model_config = ConfigDict(extra='forbid')

    meta_profiles: OrgMetaProfiles | None = None
    agent_settings: dict[str, Any] = Field(default_factory=dict)
    conversation_settings: dict[str, Any] = Field(default_factory=dict)
    v1_enabled: bool | None = None
    conversation_expiration: int | None = None
    enable_conversation_usage_detail: bool = False
    enable_shared_conversation_discovery: bool = True
    sandbox_grouping_strategy: str | None = None
    default_sandbox_spec_name: str | None = None
    member_settings_denials: dict[str, list[SettingsOperation]] = Field(
        default_factory=dict
    )
    registered_marketplaces: list[dict[str, Any]] | None = None

    @field_validator('agent_settings')
    @classmethod
    def validate_agent_document(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_agent_settings(value, persisted=True)
        return value

    @field_validator('conversation_settings')
    @classmethod
    def validate_conversation_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        ConversationSettings.from_persisted(value)
        return value


def _preference(name: str) -> Any:
    field = OrganizationSettings.model_fields[name]

    def get(org: 'Org') -> Any:
        document = org.settings or {}
        return (
            document[name]
            if name in document
            else field.get_default(call_default_factory=True)
        )

    def set_(org: 'Org', value: Any) -> None:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode='json')
        # Validate the written field without forcing unrelated legacy blocks
        # through today's SDK schema before their repair or next edit.
        change = OrganizationSettings.model_validate({name: value}).model_dump(
            mode='json', exclude_unset=True
        )
        org.settings = {**(org.settings or {}), **change}

    def expression(cls):
        value = cls.settings[name]
        if isinstance(field.default, bool):
            return func.coalesce(value.as_boolean(), field.default)
        return value.as_string() if name == 'default_sandbox_spec_name' else value

    return hybrid_property(get, set_, expr=expression)


class Org(Base):
    """Organization model."""

    __tablename__ = 'org'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    # Instance-admin lifecycle: ``active`` (default) or ``suspended``.
    # Suspended orgs remain readable by super admins but deny normal
    # member access via ``require_permission``.
    status: Mapped[str] = mapped_column(String, nullable=False, default='active')
    billing_margin: Mapped[float | None] = mapped_column(
        nullable=True, default=DEFAULT_BILLING_MARGIN
    )
    org_version: Mapped[int] = mapped_column(nullable=False, default=0)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default='{}'
    )
    remote_runtime_resource_factor = _preference('remote_runtime_resource_factor')
    enable_proactive_conversation_starters = _preference(
        'enable_proactive_conversation_starters'
    )
    sandbox_base_container_image = _preference('sandbox_base_container_image')
    sandbox_runtime_container_image = _preference('sandbox_runtime_container_image')
    meta_profiles = _preference('meta_profiles')
    agent_settings = _preference('agent_settings')
    conversation_settings = _preference('conversation_settings')
    max_budget_per_task = _preference('max_budget_per_task')
    v1_enabled = _preference('v1_enabled')
    conversation_expiration = _preference('conversation_expiration')
    enable_conversation_usage_detail = _preference('enable_conversation_usage_detail')
    enable_shared_conversation_discovery = _preference(
        'enable_shared_conversation_discovery'
    )
    sandbox_grouping_strategy = _preference('sandbox_grouping_strategy')
    default_sandbox_spec_name = _preference('default_sandbox_spec_name')
    registered_marketplaces = _preference('registered_marketplaces')
    member_settings_denials = _preference('member_settings_denials')
    # encrypted column, don't set directly, set without the underscore
    _llm_api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # encrypted column, don't set directly, set without the underscore
    _search_api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # encrypted column, don't set directly, set without the underscore
    _sandbox_api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # Source of truth for BYOR/OpenHands LLM key export entitlement.
    # Set by completed billing sessions or when positive org credits are detected.
    byor_export_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    # NULL inherits the deployment-wide daily sandbox limit.
    # Set to -1 to exempt this org from daily sandbox limits entirely
    # (for paying SaaS customers). Any other integer is the org-specific limit.
    # Physical column keeps its legacy name (see storage/user.py).
    daily_sandbox_limit: Mapped[int | None] = mapped_column(
        'daily_conversation_limit', nullable=True
    )
    # Encrypted column for LLM profiles (contains API keys)
    llm_profiles: Mapped[dict[str, Any] | None] = mapped_column(
        EncryptedJSON, nullable=True
    )
    # Encrypted column for agent profiles. Mirrors llm_profiles: the column is
    # the at-rest encryption boundary, so secret-bearing skills[].mcp_tools ride
    # in cleartext inside the encrypted blob. Envelope is
    # ``{profiles: {<id>: AgentProfile}, active: <id> | null}`` (see AgentProfiles).
    agent_profiles: Mapped[dict[str, Any] | None] = mapped_column(
        EncryptedJSON, nullable=True
    )
    # Encrypted column for shared LLM provider connections. Mirrors
    # llm_profiles: the column is the at-rest encryption boundary, so each
    # connection's api_key rides in cleartext inside the encrypted blob.
    # Envelope is ``{connections: {<id>: ProviderConnection}}`` (see
    # ProviderConnections). NULL reads back as an empty collection.
    provider_connections: Mapped[dict[str, Any] | None] = mapped_column(
        EncryptedJSON, nullable=True
    )
    # Org-scoped MCP server listings (OHE-3091). Envelope is
    # ``{servers: {<name>: {managed_by: 'org' | 'user', server: <MCPServer>}}}``
    # (see ``storage.org_mcp_servers``). Encrypted because an ``org``-managed
    # listing carries the admin's shared credential inline. Never merged into
    # ``agent_settings``: the launch path adds the listings under each member's
    # own servers (``storage.agent_profile_resolution.launch_mcp_config``).
    # NULL reads back as no listings.
    mcp_servers: Mapped[dict[str, Any] | None] = mapped_column(
        EncryptedJSON, nullable=True
    )
    # Marks the bootstrapped default org on OHE installs; a partial unique
    # index allows at most one default org per install.
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Timestamp for optimistic locking - tracks last modification
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    org_members: Mapped[list['OrgMember']] = relationship(
        'OrgMember', back_populates='org'
    )
    current_users: Mapped[list['User']] = relationship(
        'User', back_populates='current_org'
    )
    invitations: Mapped[list['OrgInvitation']] = relationship(
        'OrgInvitation', back_populates='org', passive_deletes=True
    )
    billing_sessions: Mapped[list['BillingSession']] = relationship(
        'BillingSession', back_populates='org'
    )
    stored_conversation_metadata_saas: Mapped[
        list['StoredConversationMetadataSaas']
    ] = relationship('StoredConversationMetadataSaas', back_populates='org')
    user_secrets: Mapped[list['StoredCustomSecrets']] = relationship(
        'StoredCustomSecrets', back_populates='org'
    )
    api_keys: Mapped[list['ApiKey']] = relationship('ApiKey', back_populates='org')
    slack_conversations: Mapped[list['SlackConversation']] = relationship(
        'SlackConversation', back_populates='org'
    )
    slack_users: Mapped[list['SlackUser']] = relationship(
        'SlackUser', back_populates='org'
    )
    stripe_customers: Mapped[list['StripeCustomer']] = relationship(
        'StripeCustomer', back_populates='org'
    )
    git_claims: Mapped[list['OrgGitClaim']] = relationship(
        'OrgGitClaim', back_populates='org'
    )

    @property
    def llm_api_key(self) -> SecretStr | None:
        if self._llm_api_key:
            decrypted = decrypt_value(self._llm_api_key)
            return SecretStr(decrypted)
        return None

    @llm_api_key.setter
    def llm_api_key(self, value: str | SecretStr | None):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        self._llm_api_key = encrypt_value(raw) if raw else None

    @property
    def search_api_key(self) -> SecretStr | None:
        if self._search_api_key:
            decrypted = decrypt_value(self._search_api_key)
            return SecretStr(decrypted)
        return None

    @search_api_key.setter
    def search_api_key(self, value: str | SecretStr | None):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        self._search_api_key = encrypt_value(raw) if raw else None

    @property
    def sandbox_api_key(self) -> SecretStr | None:
        if self._sandbox_api_key:
            decrypted = decrypt_value(self._sandbox_api_key)
            return SecretStr(decrypted)
        return None

    @sandbox_api_key.setter
    def sandbox_api_key(self, value: str | SecretStr | None):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        self._sandbox_api_key = encrypt_value(raw) if raw else None
