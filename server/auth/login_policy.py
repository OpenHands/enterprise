"""Deployment policy for the managed Keycloak corporate SSO path."""

import os

from fastapi import HTTPException


def enforce_enterprise_sso() -> bool:
    return os.getenv('ENFORCE_ENTERPRISE_SSO', 'false').lower() in ('true', '1')


def require_native_login_allowed() -> None:
    # Native OAuth/SAML/password logins are a separate authentication stack.
    # They must not mint a parallel application session when Keycloak SSO is
    # required. Existing sessions and API keys are not revoked by this policy.
    if enforce_enterprise_sso():
        raise HTTPException(
            403, 'Sign in through the configured enterprise SSO provider'
        )
