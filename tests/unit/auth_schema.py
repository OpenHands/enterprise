"""The account schema used by isolated SQLite authentication unit tests."""

from sqlalchemy.engine import Connection, Engine

from storage.api_key import ApiKey
from storage.auth_action_tokens import AuthActionToken
from storage.auth_sessions import AuthSession
from storage.auth_tokens import AuthTokens
from storage.base import Base
from storage.external_identities import ExternalIdentity
from storage.installation_auth import InstallationAuth
from storage.local_credentials import LocalCredentials
from storage.org import Org
from storage.org_invitation import OrgInvitation
from storage.org_member import OrgMember
from storage.role import Role
from storage.slack_user import SlackUser
from storage.stored_offline_token import StoredOfflineToken
from storage.user import User
from storage.user_authorization import UserAuthorization
from storage.user_settings import UserSettings


def create_auth_schema(bind: Connection | Engine) -> None:
    """Create authentication tables and their account/organization dependencies.

    Integration-specific tables, such as GitLab's PostgreSQL ARRAY columns,
    are outside these tests and are exercised by the shared PostgreSQL fixtures.
    """
    Base.metadata.create_all(
        bind,
        tables=[
            model.__table__
            for model in (
                ApiKey,
                AuthActionToken,
                AuthSession,
                AuthTokens,
                ExternalIdentity,
                InstallationAuth,
                LocalCredentials,
                Org,
                OrgInvitation,
                OrgMember,
                Role,
                SlackUser,
                StoredOfflineToken,
                User,
                UserAuthorization,
                UserSettings,
            )
        ],
    )
