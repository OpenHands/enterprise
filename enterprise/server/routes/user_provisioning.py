"""
Admin endpoint for provisioning users directly into an organization.

This is a privileged operation: it bypasses the normal sign-up flow
(email verification, TOS acceptance, OAuth IDP round-trip) and creates
a ready-to-use account on behalf of an org admin. Access is gated by
the ``PROVISION_USER`` permission, which is granted to org-scoped
``owner`` and ``admin`` roles and explicit instance-level super roles
(see ``server.auth.authorization``).

The endpoint is **idempotent**. If the requested email already maps
to a Keycloak user *and* an OpenHands ``User`` row, the route skips
the create steps and only ensures the user is a member of the target
org and has a usable API key. This is the recovery path OEM partners
need when a provisioned user has lost their API key or needs to be
added to a second organization.

Flow (POST ``/api/organizations/provision-user``):

Pre-flight:

0. Resolve the target org from the caller's API key org or the
   ``X-Org-Id`` header (validated against the caller's memberships
   by ``require_permission``). Reject personal workspaces.
1. Look up the email in Keycloak (``get_user_id_from_user_email``)
   and in the OpenHands DB (``UserStore.get_user_by_email``) to
   determine which branch to take:

   - **Neither exists** — full 6-step create (case a, 201 Created).
   - **Both exist** — idempotent re-provision (case b, 200 OK):
     skip Keycloak create, offline-token store, and ``create_user``;
     only ensure target-org membership + return/reissue API key.
   - **Keycloak only** — recovery: re-attach the OpenHands DB to
     the existing Keycloak user via ``UserStore.create_user`` (it
     is idempotent on the ``sub``), then proceed as case b.
   - **OpenHands DB only** — fail with a structured 409; manual
     Keycloak intervention is required to repair the split state.
   - **Late-arriving 409 from a concurrent Keycloak create** —
     re-run the pre-check, then fall through to case b.

Create branch (case a / case c):

2. Create the user in Keycloak (realm configured by
   ``KEYCLOAK_REALM_NAME``), pre-verifying the email and setting a
   non-temporary password.
3. Request an offline refresh token for the new user via ROPC and
   store it via ``TokenManager.store_offline_token`` so the account
   can be used for backend operations (e.g. SDK calls) immediately.
4. Create the OpenHands user record (mirroring the ``keycloak_callback``
   shape), but with ``email_verified=True``, ``accepted_tos=now()``, and
   ``user_consents_to_analytics=True`` — no UI round-trips required.
5. Set up a LiteLLM integration for the user in the target org and add
   them with the requested role (``member`` by default, ``admin``, or ``owner``).
6. Mint an API key bound to the target org and return it to the caller.

Membership and API key (all branches):

7. If the user is not yet a member of the target org, add them via
   ``OrgMemberStore.add_user_to_org`` after re-running
   ``create_litellm_integration`` so the LiteLLM team membership is
   also in place.
8. Resolve the API key: if a key with the requested name already
   exists for this user/org, return it (default, idempotent read);
   only mint a fresh key — after deleting the old one — when the
   caller explicitly opts in via ``reissue_api_key=True``.

The caller receives the email, password (only on a true create —
``None`` on idempotent re-provisions), and the API key in a single
response. The ``action`` field on the response distinguishes the
branch that was taken (``created`` / ``added_to_org`` /
``reprovisioned``) for audit and client-side handling.

On failure after a state change, ``_rollback_partial_provision``
compensates whatever subset of steps *this call* actually created —
see its docstring for the full unwind order. Pre-existing
Keycloak users, OpenHands DB rows, and memberships are left alone
on the idempotent path.

**Compensation strategy.** Each post-Keycloak side effect is tracked
by local progress variables so the unwind path targets exactly the
state that *this call* created:

* ``keycloak_user_created`` — set once ``create_keycloak_user``
  returns successfully; gating the Keycloak delete in rollback so
  we do not destroy a user that existed before this request.
* ``personal_org_created`` — set once ``UserStore.create_user``
  actually inserts a new ``User``/``Org`` (the method is itself
  idempotent and returns the existing row if one was found, so this
  flag is False on the idempotent re-provision path).
* ``target_membership_added`` — set once
  ``OrgMemberStore.add_user_to_org`` succeeds for the target org.

The order in the unwind matters: the personal-org cascade only
deletes the User row when the user is the sole orphan of the personal
org, so the target-org membership (if added in this call) must be
removed *before* the cascade runs. See ``_rollback_partial_provision``
for the full ordering and the rationale behind it.

**Known partial-cleanup gap (offline token).** Step 3 stores the
offline token before ``UserStore.create_user`` runs, mirroring the
production OAuth flow (``keycloak_offline_callback`` stores the
token before any ``UserStore`` interaction). The rollback path
removes the Keycloak user, which makes the orphaned offline token
row harmless — it is keyed by a Keycloak ``sub`` that no longer
exists, so it cannot be used to authenticate. The inverse ordering
(user-first) would leak the *entire* OpenHands cascade instead of a
single encrypted token blob, which is strictly worse. Periodic
``OfflineTokenStore`` reconciliation (if/when added) can sweep these.

**Known partial-cleanup gap (LiteLLM target-team membership).** When
``create_litellm_integration`` succeeded for the target org, the
provisioned Keycloak ``sub`` was added to the target org's LiteLLM
team and a per-user key was minted on the LiteLLM side. Removing the
OpenHands ``OrgMember`` row does not propagate to LiteLLM, so on
rollback the LiteLLM-side membership and key for that ``sub`` are
left behind. They are functionally inert (the Keycloak account is
deleted in the same unwind, so there is no way to authenticate as
the orphaned ``sub``), but they do accumulate. Cleanup should ride
on a future ``LiteLlmManager.remove_user_from_team(sub, org_id)``
helper rather than reaching into private internals from this route.

**Password handling on idempotent re-provision.** When the email
already maps to an existing Keycloak user, the route does **not**
overwrite that user's password — Keycloak's own forgot-password flow
is the right surface for credential rotation, and silently resetting
it would break the multi-org use case where the same user may be
provisioned into multiple organizations with different owners. The
``password`` field on the response is ``None`` on the idempotent
path so the caller knows they cannot extract a credential from it.
"""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from uuid import UUID as parse_uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from keycloak.exceptions import KeycloakError
from pydantic import BaseModel, EmailStr, Field, SecretStr
from server.auth.authorization import Permission, require_permission
from server.auth.org_context import EFFECTIVE_ORG_ID
from server.auth.token_manager import TokenManager
from sqlalchemy import select
from storage.api_key_store import ApiKeyStore
from storage.database import a_session_maker
from storage.org_member_store import OrgMemberStore
from storage.org_service import OrgService
from storage.org_store import OrgStore
from storage.role_store import RoleStore
from storage.user import User
from storage.user_store import UserStore

from openhands.app_server.utils.logger import openhands_logger as logger

# Routes that read the target org from ``X-Org-Id`` rather than the URL
# path live under ``/api/organizations`` so they sit alongside the rest
# of the org-management surface, but they are intentionally separated
# from ``org_router`` (which carries the ``REJECT_X_ORG_ID_PATH_MISMATCH``
# guard for ``/{org_id}/...`` routes — that guard would no-op here, but
# keeping the routers split makes the intent explicit).
user_provisioning_router = APIRouter(prefix='/api/organizations', tags=['Orgs'])

# Roles that can be assigned directly during provisioning. Provisioning
# supports creating regular members, org admins, and org owners.
ProvisionedRoleName = Literal['member', 'admin', 'owner']
DEFAULT_PROVISIONED_ROLE: ProvisionedRoleName = 'member'

# Length of generated passwords. 24 characters from a 70-symbol alphabet
# yields well over 128 bits of entropy, which exceeds typical Keycloak
# realm password-policy minimums while staying short enough to display
# in API responses.
_GENERATED_PASSWORD_LENGTH = 24


def _utc_now_naive() -> datetime:
    """Return the current UTC time as a naive ``datetime``.

    ``User.accepted_tos`` (and similar timestamp columns on the user
    record) are stored as naive UTC datetimes, so we strip the tzinfo
    after capturing ``now`` in UTC to avoid mixed-awareness comparisons.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _generate_password(length: int = _GENERATED_PASSWORD_LENGTH) -> str:
    """Generate a strong random password suitable for Keycloak policies.

    Mixes upper/lowercase letters, digits, and a curated set of symbols
    so the result satisfies common Keycloak password-policy rules
    (digits, special characters, mixed case) without including
    characters that complicate shell/JSON usage.
    """
    alphabet = string.ascii_letters + string.digits + '!@#$%^&*-_=+'
    # Loop until we have at least one of each character class to
    # satisfy realm password policies that mandate mixed character
    # types. With a 24-character draw from this alphabet the
    # probability of any class being absent is ~10^-9, so a bounded
    # loop guarantees termination while still effectively always
    # succeeding on the first iteration.
    for _ in range(100):
        candidate = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in candidate)
            and any(c.isupper() for c in candidate)
            and any(c.isdigit() for c in candidate)
            and any(c in '!@#$%^&*-_=+' for c in candidate)
        ):
            return candidate
    # Practically unreachable: after 100 independent 24-char draws the
    # probability of never satisfying the class constraints is < 10^-87.
    raise RuntimeError(
        'Failed to generate a password satisfying character-class '
        'requirements after 100 attempts.'
    )


class ProvisionUserRequest(BaseModel):
    """Payload for ``POST /api/organizations/provision-user``."""

    email: EmailStr = Field(
        ...,
        description='Email address for the new user. Used as the Keycloak username.',
    )
    password: str | None = Field(
        default=None,
        min_length=8,
        max_length=256,
        description=(
            'Optional initial password. If omitted, a strong random '
            'password is generated and returned in the response. '
            'When supplied, this value is sent to Keycloak as-is and '
            'must satisfy the realm password policy (length, character '
            'classes, blacklist, etc.); a policy violation surfaces as '
            'a 409 from Keycloak. Generated passwords are constructed '
            'to satisfy common realm policies (mixed case + digit + '
            'symbol) — caller-supplied passwords carry no such '
            'guarantees beyond the ``min_length=8`` floor enforced here. '
            'Ignored on the idempotent re-provision path: the existing '
            "Keycloak user's password is never overwritten by this "
            'endpoint, so supplying one has no effect when the user '
            'already exists.'
        ),
    )
    api_key_name: str | None = Field(
        default=None,
        max_length=255,
        description='Optional name for the generated API key.',
    )
    role: ProvisionedRoleName = Field(
        default=DEFAULT_PROVISIONED_ROLE,
        description=(
            'Role to assign in the target organization. Provisioning supports '
            'member, admin, and owner. On the idempotent re-provision path '
            'this is the role assigned when the user is added to the target '
            'org; if they are already a member, the existing role is left '
            'untouched (use the org-member admin endpoint to change it).'
        ),
    )
    reissue_api_key: bool = Field(
        default=False,
        description=(
            'When True, delete any existing API key with the same name '
            'before minting a fresh one. Defaults to False so the '
            'endpoint is idempotent on the API-key side: re-running '
            'with the same ``api_key_name`` returns the existing key '
            'rather than invalidating it. Only set this to True when '
            'you are certain the previous key is no longer in use '
            '(e.g. it has been leaked).'
        ),
    )


# Outcome of the route. ``created`` is True only on a true first-time
# create (case a). On idempotent re-provisions (case b/c) it is False
# and the response status code is 200 OK rather than 201 Created.
ProvisionAction = Literal['created', 'added_to_org', 'reprovisioned']


class ProvisionUserResponse(BaseModel):
    """Response for ``POST /api/organizations/provision-user``.

    On a true create (``action == "created"``), ``password`` carries
    the plaintext initial credential and ``status_code`` is 201
    Created. On an idempotent re-provision (``action in
    {"added_to_org", "reprovisioned"}``), ``password`` is ``None``
    because the existing Keycloak user's password is *not* rotated by
    this endpoint — Keycloak's forgot-password flow is the right
    surface for credential rotation. The HTTP status code in those
    cases is 200 OK.

    The plaintext password (when present) is the *only* time it is
    recoverable: the endpoint bypasses the normal email-based
    set-password flow. The admin who called this endpoint is expected
    to hand the credential to the new user out-of-band (e.g. an
    internal IT system, secrets manager, or direct hand-off). Callers
    should treat the response body as sensitive: do not log it, and
    prefer TLS-terminated transport.
    """

    email: str
    password: str | None = Field(
        default=None,
        description=(
            'Plaintext initial password for the new user, when this '
            'call actually created the Keycloak account. ``None`` on '
            "idempotent re-provisions — the existing user's password "
            'is intentionally not rotated.'
        ),
    )
    api_key: str
    user_id: str
    org_id: str
    role: str
    created: bool = Field(
        description=(
            'True iff this call actually created a new user (case a). '
            'False when the user already existed and the call only '
            'ensured target-org membership and/or returned an '
            'existing API key.'
        ),
    )
    action: ProvisionAction = Field(
        description=(
            'Specific branch taken: '
            '"created" — brand-new user and org membership; '
            '"added_to_org" — user existed, just added to target org; '
            '"reprovisioned" — user existed and was already a member '
            'of the target org; only the API key was resolved.'
        ),
    )


async def _set_user_provisioned_flags(user_id: str) -> None:
    """Stamp ``email_verified``, ``accepted_tos`` and analytics consent.

    ``UserStore.create_user`` already wires up the user, personal org,
    and org-member rows. The provisioning flow then bypasses the UI
    onboarding interstitials by stamping the flags directly so the
    provisioned account is fully usable immediately. Kept as a focused
    helper to keep the route handler readable.
    """
    async with a_session_maker() as session:
        result = await session.execute(
            select(User).where(User.id == parse_uuid(user_id))
        )
        user = result.scalar_one_or_none()
        if not user:
            return
        user.email_verified = True
        user.accepted_tos = _utc_now_naive()
        user.user_consents_to_analytics = True
        # Provisioned users skip the in-product onboarding form; the
        # admin has already onboarded them out-of-band.
        user.onboarding_completed = True
        await session.commit()


def _is_keycloak_user_exists_error(error: KeycloakError) -> bool:
    """Return True iff a ``KeycloakError`` represents a user-already-exists 409.

    ``token_manager.create_keycloak_user`` calls ``a_create_user`` with
    ``exist_ok=False``; on collision Keycloak returns HTTP 409 with a
    JSON body whose ``errorMessage`` (or similar) describes the
    duplicate. We inspect the ``response_code`` first and fall back to
    a substring search on ``error_message`` so unit tests that
    construct ``KeycloakError('user already exists')`` (without
    setting ``response_code``) also match.
    """
    if error.response_code == 409:
        return True
    return 'already exists' in (error.error_message or '').lower()


@user_provisioning_router.post(
    '/provision-user',
    response_model=ProvisionUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision_user(
    body: ProvisionUserRequest,
    response: Response,
    caller_user_id: str = Depends(require_permission(Permission.PROVISION_USER)),
    target_org_id: UUID = EFFECTIVE_ORG_ID,
) -> ProvisionUserResponse:
    """Create a new user and add them to the caller's selected org.

    The target org is the API key org if an API key is used, otherwise it is
    taken from the ``X-Org-Id`` header (resolved by ``EFFECTIVE_ORG_ID``).
    The caller must hold the ``PROVISION_USER`` permission for that org.
    Org-scoped owners/admins have it, and super roles may grant it explicitly
    without org membership.

    Idempotent: re-running for an email that already maps to a
    Keycloak user *and* an OpenHands ``User`` row is a no-op on the
    identity side and returns the existing API key (or a freshly
    reissued one if ``reissue_api_key=True``). The HTTP status code
    is 201 Created on a true first-time create and 200 OK on an
    idempotent re-provision.

    Returns the email, the API key bound to the target org, and
    (on a true create) the plaintext password.
    """
    email = body.email.lower().strip()
    # Only used in the create path. Pre-generated so the same value is
    # stored in Keycloak and returned in the response — never
    # regenerated between the two calls.
    password = body.password or _generate_password()
    api_key_name = body.api_key_name or 'Initial API Key'
    provisioned_role = body.role
    reissue_api_key = body.reissue_api_key

    # Confirm the target org actually exists before we mutate Keycloak.
    # ``require_permission`` has already validated that the caller is a
    # member with the right role, but the org row could have been
    # deleted between the membership check and this code path; an
    # explicit fetch produces a clean 404 instead of a confusing 500
    # later in ``OrgService.create_litellm_integration``.
    org = await OrgStore.get_org_by_id(target_org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Target organization not found',
        )

    # Reject provisioning into a personal workspace.
    #
    # The personal-workspace invariant is ``Org.id == User.id == UUID(
    # keycloak.sub)`` (see ``UserStore.create_user``), so a personal
    # workspace is detected by comparing ``target_org_id`` to the
    # *caller's* user id. ``require_permission(PROVISION_USER)`` lets
    # the call through because every user is the owner of their own
    # personal org, but the *product* meaning of the permission is
    # "create members of a team org" — not "every user can mint extra
    # accounts in their personal workspace and receive credentials/
    # API keys for them". Mirrors the long-standing personal-workspace
    # rejection in ``server.services.org_invitation_service`` (and uses
    # the same 403 status code) so the two endpoints behave
    # consistently.
    if str(target_org_id) == caller_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Cannot provision users into a personal workspace',
        )

    token_manager = TokenManager()

    # Pre-flight: look up the user in Keycloak and in the OpenHands DB
    # so we can decide which branch to take. Both lookups are
    # best-effort and read-only; failures here are not fatal and we
    # fall through to the create path.
    existing_kc_user_id = await token_manager.get_user_id_from_user_email(email)
    existing_oh_user = await UserStore.get_user_by_email(email)

    if existing_oh_user is not None and existing_kc_user_id is None:
        # OpenHands DB row exists but no Keycloak user — split state
        # that we cannot safely auto-repair. Manually re-creating the
        # Keycloak account would orphan the existing User row, and
        # silently failing would leave the OEM partner stuck. Surface
        # the conflict with enough detail for an operator to repair.
        logger.error(
            'provision_user:split_state_db_only',
            extra={
                'caller_user_id': caller_user_id,
                'target_org_id': str(target_org_id),
                'email': email,
                'openhands_user_id': str(existing_oh_user.id),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                'User exists in the OpenHands database but has no '
                'corresponding Keycloak account. Manual Keycloak '
                'intervention is required to repair the split state.'
            ),
        )

    # Map the existence states to the case branches described in the
    # module docstring.
    if existing_kc_user_id is None and existing_oh_user is None:
        case = 'create'
    elif existing_kc_user_id is not None and existing_oh_user is None:
        case = 'recover'
    else:
        case = 'idempotent'

    # ---------------------------------------------------------------------------
    # Identity-establishment branch: actually create the Keycloak user,
    # the offline token, and the OpenHands User row. ``UserStore.create_user``
    # is idempotent on the Keycloak sub, so the recover case reuses it
    # to attach a freshly-discovered Keycloak ``sub`` to the OpenHands DB.
    # ---------------------------------------------------------------------------
    openhands_user_id: UUID | None = None
    keycloak_user_created = False
    personal_org_created = False
    # Always declared so the rollback path can reference it even when
    # the failure happens before the membership-add step runs (e.g.
    # failure during ``_set_user_provisioned_flags`` on the create
    # path, or any failure on the idempotent path).
    target_membership_added = False
    kc_user_id: str | None = None

    if case in ('create', 'recover'):
        if case == 'recover':
            kc_user_id = existing_kc_user_id
        else:
            try:
                kc_user_id = await token_manager.create_keycloak_user(
                    email=email,
                    password=password,
                    email_verified=True,
                )
                keycloak_user_created = True
            except KeycloakError as e:
                # TOCTOU: the pre-check raced with a concurrent
                # provision-user for the same email. Re-query
                # Keycloak; if the user is now there, fall through to
                # the idempotent branch instead of failing.
                if _is_keycloak_user_exists_error(e):
                    recovered_kc_id = await token_manager.get_user_id_from_user_email(
                        email
                    )
                    if recovered_kc_id is not None:
                        logger.info(
                            'provision_user:toctou_fallthrough',
                            extra={
                                'caller_user_id': caller_user_id,
                                'target_org_id': str(target_org_id),
                                'email': email,
                                'recovered_kc_user_id': recovered_kc_id,
                            },
                        )
                        kc_user_id = recovered_kc_id
                        case = 'idempotent'
                    else:
                        logger.warning(
                            'provision_user:keycloak_create_failed_no_recovery',
                            extra={
                                'caller_user_id': caller_user_id,
                                'target_org_id': str(target_org_id),
                                'email': email,
                                'error': str(e),
                            },
                        )
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                'Failed to create Keycloak user (it may '
                                'already exist) and could not be recovered'
                            ),
                        ) from e
                else:
                    logger.warning(
                        'provision_user:keycloak_create_failed',
                        extra={
                            'caller_user_id': caller_user_id,
                            'target_org_id': str(target_org_id),
                            'email': email,
                            'error': str(e),
                        },
                    )
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail='Failed to create Keycloak user',
                    ) from e
            except Exception:
                logger.exception(
                    'provision_user:keycloak_create_unexpected',
                    extra={
                        'caller_user_id': caller_user_id,
                        'target_org_id': str(target_org_id),
                        'email': email,
                    },
                    stack_info=True,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail='Failed to create Keycloak user',
                )

    if case == 'idempotent':
        # TOCTOU recovery already populated ``kc_user_id`` with the
        # freshly-discovered Keycloak id; only refresh ``kc_user_id``
        # from the stale pre-check if we did *not* take the recovery
        # branch.
        if kc_user_id is None:
            kc_user_id = existing_kc_user_id
        # TOCTOU recovery can also land in the idempotent case via
        # ``case = 'idempotent'`` assignment inside the recovery
        # branch above. Re-read the OpenHands ``User`` row once now so
        # subsequent steps (``OrgMemberStore.get_org_member``,
        # ``api_key`` retrieval) have an up-to-date view.
        if openhands_user_id is None:
            existing_oh_user = await UserStore.get_user_by_email(email)
            if existing_oh_user is None:
                # The Keycloak user exists but the OpenHands DB has
                # not yet caught up (the concurrent winner is still
                # running). Treat this as split state to surface to
                # the operator rather than silently creating a new
                # personal org for an existing Keycloak identity.
                logger.error(
                    'provision_user:toctou_partial_state',
                    extra={
                        'caller_user_id': caller_user_id,
                        'target_org_id': str(target_org_id),
                        'email': email,
                        'kc_user_id': kc_user_id,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        'Concurrent provision detected: Keycloak user '
                        'exists but the OpenHands database row has '
                        'not yet been written. Retry the request.'
                    ),
                )
            openhands_user_id = existing_oh_user.id

    try:
        if case in ('create', 'recover'):
            # 2. Get an offline token for the new user and store it.
            # This mirrors what ``keycloak_offline_callback`` does at
            # the end of the interactive flow so the user is
            # immediately usable for backend operations. Skipped on
            # the idempotent path: the existing user already has an
            # offline token, and we cannot ROPC-authenticate without
            # the plaintext password (which we deliberately do not
            # store on re-provision).
            if case == 'create':
                offline_token = await token_manager.request_offline_token(
                    username=email, password=password
                )
                # ``kc_user_id`` is non-None here: the Keycloak create
                # above set it on success, or the recover branch
                # copied it from ``existing_kc_user_id``. The create
                # path raises ``HTTPException`` on failure, which
                # propagates to the outer ``except`` and bypasses this
                # line. Bind a strictly-typed local so the rest of
                # this block reads ``str`` rather than ``str | None``.
                assert kc_user_id is not None
                block_kc_user_id: str = kc_user_id
                await token_manager.store_offline_token(
                    user_id=block_kc_user_id, offline_token=offline_token
                )
            else:
                # ``case == 'recover'`` — Keycloak user existed
                # before this call, so ``kc_user_id`` was set in the
                # recover branch above.
                assert kc_user_id is not None
                block_kc_user_id = kc_user_id

            # 3. Create the OpenHands user. ``UserStore.create_user``
            # is idempotent on the Keycloak ``sub`` (returns the
            # existing row if found), so the recover case reuses it
            # to attach the freshly-discovered Keycloak identity to
            # the OpenHands DB.
            user_info_dict = {
                'sub': block_kc_user_id,
                'email': email,
                'email_verified': True,
                'preferred_username': email,
            }
            new_user = await UserStore.create_user(block_kc_user_id, user_info_dict)
            if new_user is None:
                raise RuntimeError('UserStore.create_user returned None')
            personal_org_created = new_user.id != (
                existing_oh_user.id if existing_oh_user else None
            )
            openhands_user_id = new_user.id

            # 4. Stamp TOS / verification / consent flags so the
            # provisioned user does not get bounced to the
            # email-verification or TOS interstitials on first login.
            await _set_user_provisioned_flags(block_kc_user_id)

        # 5. Add the user to the *target* org if they are not already a
        # member. Skipped when the caller-selected org happens to be
        # the user's personal org (id == sub) — the personal-org
        # owner membership was just created by ``UserStore.create_user``
        # and re-adding it would violate the ``(org_id, user_id)``
        # uniqueness constraint. Skipped on the idempotent path when
        # ``OrgMemberStore.get_org_member`` confirms an existing row.
        #
        # Reaching this block requires both ``kc_user_id`` and
        # ``openhands_user_id`` to be populated — any failure above
        # would have raised an ``HTTPException`` and been caught by
        # the outer ``except`` below, bypassing this section. The
        # ``resolved_*`` locals narrow the ``Optional`` types so
        # downstream call sites do not need to re-assert.
        assert kc_user_id is not None
        assert openhands_user_id is not None
        resolved_kc_user_id: str = kc_user_id
        resolved_oh_user_id: UUID = openhands_user_id
        if target_org_id != resolved_oh_user_id:
            existing_member = await OrgMemberStore.get_org_member(
                org_id=target_org_id, user_id=resolved_oh_user_id
            )
            if existing_member is not None:
                logger.info(
                    'provision_user:already_member',
                    extra={
                        'caller_user_id': caller_user_id,
                        'target_org_id': str(target_org_id),
                        'openhands_user_id': str(resolved_oh_user_id),
                        'kc_user_id': resolved_kc_user_id,
                    },
                )
            else:
                settings = await OrgService.create_litellm_integration(
                    target_org_id, resolved_kc_user_id
                )
                llm_api_key_secret = settings.agent_settings.llm.api_key
                # ``api_key`` is typed ``str | SecretStr | None`` on
                # the SDK side; org_invitation_service.py handles it
                # the same way. Defaulting to empty string lets
                # LiteLLM-disabled deployments still create
                # memberships.
                if llm_api_key_secret is None:
                    llm_api_key = ''
                elif isinstance(llm_api_key_secret, SecretStr):
                    llm_api_key = llm_api_key_secret.get_secret_value()
                else:
                    llm_api_key = llm_api_key_secret

                role = await RoleStore.get_role_by_name(provisioned_role)
                if role is None:
                    raise RuntimeError(
                        f'Role {provisioned_role!r} not found in database'
                    )

                await OrgMemberStore.add_user_to_org(
                    org_id=target_org_id,
                    user_id=resolved_oh_user_id,
                    role_id=role.id,
                    llm_api_key=llm_api_key,
                    status='active',
                    agent_settings_diff={},
                    conversation_settings_diff={},
                )
                target_membership_added = True

        # 6. Resolve the API key. Default is idempotent (return the
        # existing key if one with the same name exists); the caller
        # opts into a fresh key by setting ``reissue_api_key=True``,
        # which deletes the old key first.
        api_key_store = ApiKeyStore.get_instance()
        existing_api_key = await api_key_store.retrieve_api_key_by_name(
            user_id=resolved_kc_user_id, name=api_key_name
        )
        if existing_api_key is not None:
            if reissue_api_key:
                deleted = await api_key_store.delete_api_key_by_name(
                    user_id=resolved_kc_user_id,
                    name=api_key_name,
                    org_id=target_org_id,
                )
                if not deleted:
                    logger.warning(
                        'provision_user:reissue_delete_failed',
                        extra={
                            'caller_user_id': caller_user_id,
                            'kc_user_id': resolved_kc_user_id,
                            'api_key_name': api_key_name,
                            'target_org_id': str(target_org_id),
                        },
                    )
                api_key = await api_key_store.create_api_key(
                    user_id=resolved_kc_user_id,
                    name=api_key_name,
                    org_id=target_org_id,
                )
            else:
                api_key = existing_api_key
        else:
            api_key = await api_key_store.create_api_key(
                user_id=resolved_kc_user_id,
                name=api_key_name,
                org_id=target_org_id,
            )
    except HTTPException:
        # FastAPI HTTPException is intentional — surface as-is, but
        # still attempt to clean up whatever post-Keycloak state we
        # created so we do not orphan a half-created identity.
        await _rollback_partial_provision(
            token_manager=token_manager,
            kc_user_id=kc_user_id,
            openhands_user_id=openhands_user_id,
            target_org_id=target_org_id,
            target_membership_added=target_membership_added,
            keycloak_user_created=keycloak_user_created,
            personal_org_created=personal_org_created,
        )
        raise
    except Exception as e:
        logger.exception(
            'provision_user:post_keycloak_failure',
            extra={
                'caller_user_id': caller_user_id,
                'target_org_id': str(target_org_id),
                'kc_user_id': kc_user_id,
                'email': email,
                'case': case,
            },
            stack_info=True,
        )
        await _rollback_partial_provision(
            token_manager=token_manager,
            kc_user_id=kc_user_id,
            openhands_user_id=openhands_user_id,
            target_org_id=target_org_id,
            target_membership_added=target_membership_added,
            keycloak_user_created=keycloak_user_created,
            personal_org_created=personal_org_created,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to finish provisioning user',
        ) from e

    # Map (case, target_membership_added) to the response action.
    # - create + membership_added (or implicitly added because target
    #   is the personal org) -> "created"
    # - any other case + membership_added -> "added_to_org"
    # - any case + membership_already_existed -> "reprovisioned"
    if case == 'create':
        action: ProvisionAction = 'created'
    elif target_membership_added:
        action = 'added_to_org'
    else:
        action = 'reprovisioned'

    if action == 'created':
        response.status_code = status.HTTP_201_CREATED
    else:
        response.status_code = status.HTTP_200_OK

    logger.info(
        f'provision_user:{action}',
        extra={
            'caller_user_id': caller_user_id,
            'kc_user_id': kc_user_id,
            'openhands_user_id': str(openhands_user_id) if openhands_user_id else None,
            'target_org_id': str(target_org_id),
            'provisioned_role': provisioned_role,
            'case': case,
            'reissue_api_key': reissue_api_key,
            # Intentionally omit email/password from the log line; the
            # full audit trail of who provisioned whom is captured by
            # caller_user_id + kc_user_id.
        },
    )

    # Narrow for the response builder: every code path that reaches this
    # point has either set ``kc_user_id`` (create / TOCTOU recovery)
    # or copied it from the pre-check (recover / idempotent). Reaching
    # the response with ``kc_user_id is None`` would be a logic bug.
    assert kc_user_id is not None
    final_kc_user_id: str = kc_user_id
    return ProvisionUserResponse(
        email=email,
        # Only set on a true first-time create — the existing
        # Keycloak user's password is intentionally never rotated on
        # the idempotent path.
        password=password if action == 'created' else None,
        api_key=api_key,
        user_id=final_kc_user_id,
        org_id=str(target_org_id),
        role=provisioned_role,
        created=action == 'created',
        action=action,
    )


async def _rollback_partial_provision(
    *,
    token_manager: TokenManager,
    kc_user_id: str | None,
    openhands_user_id: UUID | None,
    target_org_id: UUID,
    target_membership_added: bool,
    keycloak_user_created: bool = False,
    personal_org_created: bool = False,
) -> None:
    """Best-effort rollback of a partially completed provision.

    Runs on the unwind path of an already-failed request. Every step is
    wrapped individually because we never want a secondary cleanup
    failure to mask the original provisioning error — the underlying
    helpers each log their own diagnostics, so swallowing here is
    intentional.

    Two flags gate what gets torn down:

    * ``keycloak_user_created`` — True iff *this* call successfully
      ran ``create_keycloak_user``; gating the Keycloak delete so we
      do not destroy a user that existed before this request (case b
      idempotent, TOCTOU fallthrough, case c recover).
    * ``personal_org_created`` — True iff ``UserStore.create_user``
      actually inserted a new ``User``/``Org`` in this call. The method
      is itself idempotent (returns the existing row when found), so
      this flag is False on the idempotent re-provision path.

    Unwind order matters:

    1. **Target-org membership.** If *this* call inserted the
       target-org ``OrgMember`` row, the user has memberships in *both*
       the personal org and the target org. ``OrgStore.delete_org_cascade``
       only cascade-deletes the ``User`` row when the user is the sole
       orphan of the org being deleted — a surviving target-org
       membership would cause the cascade to just reassign
       ``current_org_id`` to the target org and leave the user row
       behind. So we drop the target-org membership first.

       The freshly minted API key (step 6) is bound to ``target_org_id``
       and there is no failure step *after* the API key insert: if
       ``create_api_key`` raises mid-INSERT the row is never committed,
       and if it returns successfully the route never reaches this
       unwind. So the unwind does not need an explicit
       ``ApiKeyStore.delete`` call.

    2. **Personal-org cascade.** ``delete_org_cascade(personal_org_id,
       requester_user_id=kc_user_id)`` wipes the personal ``Org`` row,
       the owner ``OrgMember`` row, the personal-org LiteLLM team,
       org-scoped tables (``api_keys WHERE org_id =
       personal_org_id``, ``conversation_metadata_saas``,
       ``billing_sessions``, etc.) and — because the user is now sole
       orphan after step 1 — the ``User`` row itself, in a single
       transaction. ``requester_user_id`` must equal the deleted
       user's ``id`` so the cascade treats this as a personal-org
       self-service deletion rather than raising
       ``OrphanedUserError``.

    3. **Keycloak user.** Last, so the local OpenHands identity is
       gone before we drop the upstream identity. Re-doing the
       Keycloak delete via the existing ``delete_keycloak_user``
       helper keeps that retry/logging behaviour consistent with
       interactive-flow cleanup elsewhere.
    """
    # 1. Target-org artifacts. Skip silently if the membership was
    # never inserted — there is nothing to undo. Also skip when the
    # user pre-existed (case b/c): the membership was already there
    # before this call and must not be removed on rollback.
    if openhands_user_id is not None and target_membership_added:
        try:
            await OrgMemberStore.remove_user_from_org(target_org_id, openhands_user_id)
        except Exception:
            logger.exception(
                'provision_user:rollback_remove_target_membership_failed',
                extra={
                    'kc_user_id': kc_user_id,
                    'target_org_id': str(target_org_id),
                    'openhands_user_id': str(openhands_user_id),
                },
                stack_info=True,
            )

    # 2. Personal-org cascade. Only run when *this* call created the
    # personal org (``personal_org_created``); on the idempotent path
    # the personal org pre-existed and the cascade would destroy the
    # user's actual account.
    if (
        personal_org_created
        and openhands_user_id is not None
        and kc_user_id is not None
    ):
        try:
            await OrgStore.delete_org_cascade(
                openhands_user_id, requester_user_id=kc_user_id
            )
        except Exception:
            logger.exception(
                'provision_user:rollback_delete_personal_org_failed',
                extra={
                    'kc_user_id': kc_user_id,
                    'openhands_user_id': str(openhands_user_id),
                },
                stack_info=True,
            )

    # 3. Keycloak user. Only delete when *this* call created the
    # Keycloak identity (``keycloak_user_created``); on the
    # idempotent / recover paths the Keycloak user pre-existed and
    # must be left alone. Always runs last so the local OpenHands
    # identity is gone before we drop the upstream identity.
    if keycloak_user_created and kc_user_id is not None:
        try:
            await token_manager.delete_keycloak_user(kc_user_id)
        except Exception:
            logger.debug(
                'provision_user:rollback_delete_keycloak_user_failed',
                extra={'kc_user_id': kc_user_id},
            )
