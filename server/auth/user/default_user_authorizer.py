import logging
from dataclasses import dataclass
from typing import AsyncGenerator

from fastapi import Request
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from openhands.app_server.services.injector import InjectorState
from server.auth.contracts import AuthenticationUnavailable, UserProfile
from server.auth.email_validation import extract_base_email
from server.auth.mode import is_keycloak_enabled
from server.auth.user.user_authorizer import (
    UserAuthorizationResponse,
    UserAuthorizer,
    UserAuthorizerInjector,
)
from storage.user_authorization import UserAuthorizationType
from storage.user_authorization_store import UserAuthorizationStore

logger = logging.getLogger(__name__)


@dataclass
class DefaultUserAuthorizer(UserAuthorizer):
    """Class determining whether a user may be authorized.

    Uses the user_authorizations database table to check whitelist/blacklist rules.
    """

    prevent_duplicates: bool

    async def authorize_user(self, user_info: UserProfile) -> UserAuthorizationResponse:
        user_id = str(user_info.id)
        email = user_info.email
        provider_type = user_info.identity_provider
        try:
            if not email:
                logger.warning(f'No email provided for user_id: {user_id}')
                return UserAuthorizationResponse(
                    success=False, error_detail='missing_email'
                )

            if self.prevent_duplicates and is_keycloak_enabled():
                from server.auth.keycloak.token_manager import TokenManager

                has_duplicate = await TokenManager().check_duplicate_base_email(
                    email, user_id
                )
                if has_duplicate:
                    logger.warning(
                        f'Blocked signup attempt for email {email} - duplicate base email found',
                        extra={'user_id': user_id, 'email': email},
                    )
                    return UserAuthorizationResponse(
                        success=False, error_detail='duplicate_email'
                    )

            # Check authorization rules (whitelist takes precedence over blacklist)
            base_email = extract_base_email(email) if is_keycloak_enabled() else email
            if base_email is None:
                return UserAuthorizationResponse(
                    success=False, error_detail='invalid_email'
                )
            auth_type = await UserAuthorizationStore.get_authorization_type(
                base_email, provider_type
            )

            if auth_type == UserAuthorizationType.WHITELIST:
                logger.debug(
                    f'User {email} matched whitelist rule',
                    extra={'user_id': user_id, 'email': email},
                )
                return UserAuthorizationResponse(success=True)

            if auth_type == UserAuthorizationType.BLACKLIST:
                logger.warning(
                    f'Blocked authentication attempt for email: {email}, user_id: {user_id}'
                )
                return UserAuthorizationResponse(success=False, error_detail='blocked')

            return UserAuthorizationResponse(success=True)
        except (AuthenticationUnavailable, SQLAlchemyError):
            raise AuthenticationUnavailable(
                'Admission policy is temporarily unavailable'
            ) from None
        except Exception:
            logger.exception(
                'error authorizing user', extra={'user_id': user_id}, stack_info=True
            )
            return UserAuthorizationResponse(success=False)


class DefaultUserAuthorizerInjector(UserAuthorizerInjector):
    prevent_duplicates: bool = Field(
        default=True,
        description='Whether duplicate emails (containing +) are filtered',
    )

    async def inject(
        self, state: InjectorState, request: Request | None = None
    ) -> AsyncGenerator[UserAuthorizer, None]:
        yield DefaultUserAuthorizer(
            prevent_duplicates=self.prevent_duplicates,
        )
