"""Real-DB tests for SaasSandboxSpecAccess, the org a launch is scoped to."""

from dataclasses import dataclass
from uuid import UUID

from openhands.app_server.user.specifiy_user_context import ADMIN
from server.utils.saas_sandbox_spec_access import SaasSandboxSpecAccess
from storage import sandbox_spec_access_store as store
from storage.org import Org


@dataclass
class UserInOrg:
    org_id: UUID | None

    async def get_effective_org_id(self) -> UUID | None:
        return self.org_id


@dataclass
class ResolverRun:
    """An integration launch: the resolver org wins over the user's own org."""

    resolver_org_id: UUID
    user_org_id: UUID

    async def get_effective_org_id(self) -> UUID:
        return self.user_org_id


async def _grant(async_session_maker, name, *org_ids):
    async with async_session_maker() as session:
        await store.set_rule(session, name, True, org_ids)
        await session.commit()


async def _filter(async_session_maker, user_context, names):
    async with async_session_maker() as session:
        access = SaasSandboxSpecAccess(db_session=session, user_context=user_context)
        return await access.filter_accessible(names)


async def test_user_sees_public_specs_and_their_orgs_private_ones(
    async_session_maker, create_org
):
    mine, theirs = create_org(), create_org()
    await _grant(async_session_maker, 'mine-only', mine.id)
    await _grant(async_session_maker, 'theirs-only', theirs.id)

    visible = await _filter(
        async_session_maker,
        UserInOrg(mine.id),
        ['public', 'mine-only', 'theirs-only'],
    )

    assert visible == {'public', 'mine-only'}


async def test_resolver_launches_are_scoped_to_the_resolver_org(
    async_session_maker, create_org
):
    resolver_org, user_org = create_org(), create_org()
    await _grant(async_session_maker, 'resolver-only', resolver_org.id)
    await _grant(async_session_maker, 'user-only', user_org.id)

    visible = await _filter(
        async_session_maker,
        ResolverRun(resolver_org.id, user_org.id),
        ['resolver-only', 'user-only'],
    )

    assert visible == {'resolver-only'}


async def test_caller_without_an_org_sees_only_public_specs(
    async_session_maker, create_org
):
    org = create_org()
    await _grant(async_session_maker, 'private', org.id)

    visible = await _filter(async_session_maker, UserInOrg(None), ['private', 'public'])

    assert visible == {'public'}


async def test_admin_context_is_unscoped(async_session_maker, create_org):
    await _grant(async_session_maker, 'private', create_org().id)

    visible = await _filter(async_session_maker, ADMIN, ['private', 'public'])

    assert visible == {'private', 'public'}


async def test_default_spec_is_the_orgs_default(
    async_session_maker, session_maker, create_org
):
    org = create_org()
    with session_maker() as session:
        session.get(Org, org.id).default_sandbox_spec_name = 'large'  # type: ignore[union-attr]
        session.commit()

    async with async_session_maker() as session:
        with_org = SaasSandboxSpecAccess(
            db_session=session, user_context=UserInOrg(org.id)
        )
        without_org = SaasSandboxSpecAccess(
            db_session=session, user_context=UserInOrg(None)
        )
        admin = SaasSandboxSpecAccess(db_session=session, user_context=ADMIN)

        assert await with_org.get_default_spec_id() == 'large'
        assert await without_org.get_default_spec_id() is None
        assert await admin.get_default_spec_id() is None
