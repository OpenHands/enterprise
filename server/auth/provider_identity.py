"""Shared identity-resolution + session-cookie helpers for the OAuth v2 login
surface.

Every IDP kind that logs a user in through ``/oauth/{provider_id}/login`` --
OAuth2/OIDC (``server.routes.oauth_v2``), SAML (``server.routes.saml``), and
(indirectly, via its own cookie helper) the integrated password IDP
(``server.routes.idp``) -- ends a successful login the same way: resolve or
create the internal ``User`` from whatever the provider calls its external
subject, then mint the small ``openhands_auth`` JWT cookie recording
``user_id`` + ``idp_provider_id``. None of that logic is actually
OAuth2-specific (it was only ever written once, in ``oauth_v2.py``, because
OAuth2 was the first non-Keycloak provider kind); this module is where it
lives so SAML doesn't need to reach into another router module's internals
for it.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from fastapi import HTTPException, Request, Response, status

from openhands.app_server.utils.logger import openhands_logger as logger
from server.utils.url_utils import get_cookie_domain, get_cookie_samesite, get_web_url
from storage.auth_provider import AuthProvider
from storage.auth_provider_store import AuthProviderStore
from storage.auth_provider_user_store import AuthProviderUserStore
from storage.encrypt_utils import get_jwt_service
from storage.user_store import UserStore


async def get_provider_or_404(provider_id: int) -> AuthProvider:
    """Look up an ``auth_providers`` row by id, or raise ``404``."""
    provider = await AuthProviderStore().get_by_id(provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'OAuth provider {provider_id} not found',
        )
    return provider


def require_enabled_provider(provider: AuthProvider) -> None:
    """404 when an admin has switched ``provider`` off.

    Called at the *start* of every login/link flow, never when resolving an
    existing session: ``enabled`` gates new authentications, so flipping it
    off cannot sign out users who are already logged in through this row (and
    an admin who mis-clicks can flip it back without anyone noticing).
    """
    if not provider.enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Provider {provider.id} is disabled',
        )


# ── encrypted state / relay state ───────────────────────────────────────────


def encrypt_state(payload: dict) -> str:
    """Encrypt a login round-trip blob (JWE) and base64-encode it for
    transport.

    Used both for OAuth's ``state`` query parameter and SAML's
    ``RelayState`` form field -- the two IdP protocols hand this value back
    unmodified across the exact same kind of third-party redirect, so they
    share one opaque, tamper-proof encoding.
    """
    raw = json.dumps(payload)
    encrypted = get_jwt_service().encrypt_value(raw)
    return base64.urlsafe_b64encode(encrypted.encode()).decode()


def decrypt_state(state: str) -> dict:
    """Reverse of :func:`encrypt_state`."""
    encrypted = base64.urlsafe_b64decode(state.encode()).decode()
    raw = get_jwt_service().decrypt_value(encrypted)
    return json.loads(raw)


# ── identity resolution ──────────────────────────────────────────────────


def is_true_claim(value: object) -> bool:
    """Whether a boolean-ish claim such as ``email_verified`` asserts true.

    OIDC specifies a JSON boolean, but some IdPs send the string ``"true"``
    (Google's own ID token docs show it that way), which the boolean
    ``User.email_verified`` column would reject.
    """
    if isinstance(value, str):
        return value.strip().lower() == 'true'
    return value is True


# Claims each identity field is read from when ``attribute_mapping`` doesn't
# name one. GitHub-style APIs send a numeric ``id`` rather than an OIDC ``sub``.
_DEFAULT_OAUTH_CLAIMS: dict[str, tuple[str, ...]] = {
    'sub': ('sub', 'id', 'user_id'),
    'email': ('email',),
    'email_verified': ('email_verified',),
}


def map_oauth_claims(userinfo: dict, attribute_mapping: dict[str, str]) -> dict:
    """Reduce an OAuth userinfo / ID-token payload to the OIDC-shaped
    ``sub`` / ``email`` / ``email_verified`` dict ``resolve_or_create_user``
    takes.

    A field named in ``attribute_mapping`` is read from that claim only -- a
    configured claim that is missing yields ``None`` rather than falling back
    to the defaults, so a misconfigured provider can't silently link users by
    some other identifier.
    """
    claims = {}
    for field, defaults in _DEFAULT_OAUTH_CLAIMS.items():
        mapped = attribute_mapping.get(field)
        names = (mapped,) if mapped else defaults
        claims[field] = next(
            (userinfo[name] for name in names if userinfo.get(name) is not None),
            None,
        )
    return claims


async def resolve_or_create_user(provider: AuthProvider, userinfo: dict) -> str:
    """Resolve the internal user for a provider's asserted identity.

    ``userinfo`` is shaped like an OIDC userinfo response (``sub`` /
    ``email`` / ``email_verified``) -- OAuth callers build it with
    :func:`map_oauth_claims`, and SAML callers (``server.routes.saml``)
    build one from the assertion (``NameID`` or the mapped attribute ->
    ``sub``, the mapped attribute -> ``email``) rather than getting a second,
    SAML-specific version of this function, since the resolution logic
    itself doesn't care which protocol produced the identity.

    Looks up ``auth_provider_users`` by ``external_subject_id``. If found,
    returns the linked user id. Otherwise creates the user and links it.

    The external ``subject`` is never used as the internal ``User.id`` --
    it's an IDP-controlled opaque string with no guaranteed shape (a
    Keycloak pairwise NameID like ``G-<uuid>``, a GitHub numeric id, ...),
    whereas ``UserStore.create_user`` requires a real UUID and derives
    ``Org.id`` from it (see that docstring). A fresh ``uuid.uuid4()`` is
    minted for every new user here instead; ``subject`` only ever lives in
    ``auth_provider_users.external_subject_id``, which is what every
    subsequent login looks up by.

    Email matching: when the sub lookup misses, the new IDP asserts an
    email, and ``provider.allow_match_by_email`` is set, the identity is
    linked to the existing ``User`` with that email. The provider flag is the
    admin's statement that this IDP's emails are trustworthy -- the IDP is
    the source of truth for who the user is, so ``email_verified`` is not
    consulted. Duplicate emails (>1 user) never auto-link -- the caller gets
    a 409 so an operator can resolve manually.

    ``AuthProvider.allow_registration`` gates only the final "mint a
    brand-new user" fallback below -- a ``403`` there means this *specific*
    provider does not accept new sign-ups, not that the account can never be
    created (an admin-issued sign-up link, or a different provider with
    ``allow_registration=True``, still can). The subject-link and
    email-match lookups above always run regardless of this flag, since they
    resolve to an *existing* user rather than registering one.
    """
    subject = userinfo.get('sub')
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Provider did not return a subject identifier',
        )
    subject = str(subject)
    link_store = AuthProviderUserStore()
    existing = await link_store.get(provider.id, subject)
    if existing is not None:
        return str(existing.user_id)

    # Stored lowercased: every email lookup in ``UserStore`` compares against
    # ``email.lower()``, so a mixed-case row could never be matched later.
    email = (userinfo.get('email') or '').strip().lower() or None
    email_verified = is_true_claim(userinfo.get('email_verified'))
    if email and provider.allow_match_by_email:
        dup_count = await UserStore.count_users_with_email(email)
        if dup_count > 1:
            logger.warning(
                'provider_identity:email_match_skipped_duplicate email=%s count=%d',
                email,
                dup_count,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=('Multiple users share this email; manual resolution required.'),
            )
        matched = await UserStore.get_user_by_email(email)
        if matched is not None:
            await link_store.link(
                oauth_provider_id=provider.id,
                user_id=matched.id,
                external_subject_id=subject,
                external_email=email,
            )
            logger.info(
                'provider_identity:email_match_linked user_id=%s provider=%s subject=%s',
                matched.id,
                provider.id,
                subject,
            )
            return str(matched.id)

    if not provider.allow_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                'This provider does not accept new registrations; '
                'only an already-linked account may sign in through it.'
            ),
        )

    user_info_dict = {
        'email': email,
        'email_verified': email_verified,
    }
    new_user_id = str(uuid.uuid4())
    user = await UserStore.create_user(new_user_id, user_info_dict)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to create user from provider identity',
        )
    await link_store.link(
        oauth_provider_id=provider.id,
        user_id=user.id,
        external_subject_id=subject,
        external_email=email,
    )
    return str(user.id)


# ── session cookie ───────────────────────────────────────────────────────


def set_oauth_v2_cookie(
    request: Request,
    response: Response,
    user_id: str,
    access_token_expires_at: datetime | None,
    accepted_tos: bool,
    idp_provider_id: int | None,
    refresh_token_expires_at: datetime | None = None,
) -> None:
    """Set the small JWT cookie for the OAuth v2 login path.

    The cookie carries only ``user_id``, ``access_token_expires_at``,
    ``accepted_tos``, and ``idp_provider_id`` -- not the tokens themselves.
    ``Max-Age`` is the IDP refresh-token expiry (or a 30-day cap if
    ``None``), per the Phase 2 spec. ``access_token_expires_at`` /
    ``refresh_token_expires_at`` are OAuth2-only concepts -- SAML and the
    integrated password IDP always pass ``None`` for both, which is exactly
    the existing "no external token to refresh" case this was already built
    to handle.
    """
    from server.auth.oauth_v2_refresh import (
        compute_cookie_max_age,
        create_oauth_v2_cookie_payload,
        sign_oauth_v2_cookie,
    )

    max_age_seconds = compute_cookie_max_age(refresh_token_expires_at)
    payload = create_oauth_v2_cookie_payload(
        user_id, access_token_expires_at, accepted_tos, idp_provider_id
    )
    signed = sign_oauth_v2_cookie(payload, max_age_seconds)
    web_url = get_web_url(request)
    response.set_cookie(
        key='openhands_auth',
        value=signed,
        max_age=max_age_seconds,
        domain=get_cookie_domain(),
        secure=web_url.startswith('https'),
        httponly=True,
        samesite=get_cookie_samesite(),
    )


__all__ = [
    'get_provider_or_404',
    'require_enabled_provider',
    'encrypt_state',
    'decrypt_state',
    'map_oauth_claims',
    'resolve_or_create_user',
    'set_oauth_v2_cookie',
]
