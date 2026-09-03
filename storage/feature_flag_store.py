"""Store class for managing feature flags and their targeting rules.

Mirrors the async ``_internal(session)`` / ``public(session=None)`` overload
pattern of ``user_authorization_store`` so callers may pass a shared session
or let the store open its own via ``a_session_maker``.
"""

from typing import Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database import a_session_maker
from storage.feature_flag import (
    FeatureFlag,
    FeatureFlagRule,
    FeatureFlagRuleEffect,
)


class FeatureFlagStore:
    """Store for managing feature flags and rules."""

    @staticmethod
    async def _get_flag(
        key: str,
        session: AsyncSession,
    ) -> FeatureFlag | None:
        result = await session.execute(
            select(FeatureFlag).where(FeatureFlag.key == key)
        )
        return result.scalars().first()

    @staticmethod
    async def get_flag(
        key: str,
        session: Optional[AsyncSession] = None,
    ) -> FeatureFlag | None:
        """Fetch a flag by its unique key."""
        if session is not None:
            return await FeatureFlagStore._get_flag(key, session)
        async with a_session_maker() as new_session:
            return await FeatureFlagStore._get_flag(key, new_session)

    @staticmethod
    async def _list_flags(session: AsyncSession) -> list[FeatureFlag]:
        result = await session.execute(select(FeatureFlag))
        return list(result.scalars().all())

    @staticmethod
    async def list_flags(
        session: Optional[AsyncSession] = None,
    ) -> list[FeatureFlag]:
        """List all flags."""
        if session is not None:
            return await FeatureFlagStore._list_flags(session)
        async with a_session_maker() as new_session:
            return await FeatureFlagStore._list_flags(new_session)

    @staticmethod
    async def _create_flag(
        key: str,
        description: str | None,
        enabled: bool,
        session: AsyncSession,
    ) -> FeatureFlag:
        flag = FeatureFlag(key=key, description=description, enabled=enabled)
        session.add(flag)
        await session.flush()
        await session.refresh(flag)
        return flag

    @staticmethod
    async def create_flag(
        key: str,
        description: str | None = None,
        enabled: bool = False,
        session: Optional[AsyncSession] = None,
    ) -> FeatureFlag:
        """Create a new flag. Raises ValueError if the key already exists."""
        if session is not None:
            existing = await FeatureFlagStore._get_flag(key, session)
            if existing is not None:
                raise ValueError(f'Flag with key {key!r} already exists')
            return await FeatureFlagStore._create_flag(
                key, description, enabled, session
            )
        async with a_session_maker() as new_session:
            existing = await FeatureFlagStore._get_flag(key, new_session)
            if existing is not None:
                raise ValueError(f'Flag with key {key!r} already exists')
            flag = await FeatureFlagStore._create_flag(
                key, description, enabled, new_session
            )
            await new_session.commit()
            return flag

    @staticmethod
    async def _update_flag(
        flag: FeatureFlag,
        description: str | None,
        enabled: bool | None,
        session: AsyncSession,
    ) -> FeatureFlag:
        if description is not None:
            flag.description = description
        if enabled is not None:
            flag.enabled = enabled
        await session.flush()
        await session.refresh(flag)
        return flag

    @staticmethod
    async def update_flag(
        key: str,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
        session: Optional[AsyncSession] = None,
    ) -> FeatureFlag | None:
        """Update a flag's description and/or enabled state.

        Returns the updated flag, or None if the flag does not exist.
        Only fields explicitly provided (not None) are modified.
        """
        if session is not None:
            flag = await FeatureFlagStore._get_flag(key, session)
            if flag is None:
                return None
            return await FeatureFlagStore._update_flag(
                flag, description, enabled, session
            )
        async with a_session_maker() as new_session:
            flag = await FeatureFlagStore._get_flag(key, new_session)
            if flag is None:
                return None
            updated = await FeatureFlagStore._update_flag(
                flag, description, enabled, new_session
            )
            await new_session.commit()
            return updated

    @staticmethod
    async def _delete_flag(flag: FeatureFlag, session: AsyncSession) -> None:
        # Delete rules explicitly so the cascade works even on backends (e.g.
        # SQLite) that do not enforce FK ondelete=CASCADE.
        await session.execute(
            delete(FeatureFlagRule).where(FeatureFlagRule.flag_id == flag.id)
        )
        await session.delete(flag)
        await session.flush()

    @staticmethod
    async def delete_flag(
        key: str,
        session: Optional[AsyncSession] = None,
    ) -> bool:
        """Delete a flag and its rules. Returns True if deleted, False if not found."""
        if session is not None:
            flag = await FeatureFlagStore._get_flag(key, session)
            if flag is None:
                return False
            await FeatureFlagStore._delete_flag(flag, session)
            return True
        async with a_session_maker() as new_session:
            flag = await FeatureFlagStore._get_flag(key, new_session)
            if flag is None:
                return False
            await FeatureFlagStore._delete_flag(flag, new_session)
            await new_session.commit()
            return True

    @staticmethod
    async def _list_rules(
        flag_id: int,
        session: AsyncSession,
    ) -> list[FeatureFlagRule]:
        result = await session.execute(
            select(FeatureFlagRule)
            .where(FeatureFlagRule.flag_id == flag_id)
            .order_by(FeatureFlagRule.priority.desc(), FeatureFlagRule.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_rules(
        flag_key: str,
        session: Optional[AsyncSession] = None,
    ) -> list[FeatureFlagRule]:
        """List all rules for a flag, ordered by priority desc then id asc."""
        if session is not None:
            flag = await FeatureFlagStore._get_flag(flag_key, session)
            if flag is None:
                return []
            return await FeatureFlagStore._list_rules(flag.id, session)
        async with a_session_maker() as new_session:
            flag = await FeatureFlagStore._get_flag(flag_key, new_session)
            if flag is None:
                return []
            return await FeatureFlagStore._list_rules(flag.id, new_session)

    @staticmethod
    async def _create_rule(
        flag_id: int,
        effect: FeatureFlagRuleEffect,
        user_id: str | None,
        org_id: str | None,
        email_pattern: str | None,
        percentage: float | None,
        priority: int,
        session: AsyncSession,
    ) -> FeatureFlagRule:
        rule = FeatureFlagRule(
            flag_id=flag_id,
            effect=effect.value,
            user_id=user_id,
            org_id=org_id,
            email_pattern=email_pattern,
            percentage=percentage,
            priority=priority,
        )
        session.add(rule)
        await session.flush()
        await session.refresh(rule)
        return rule

    @staticmethod
    async def create_rule(
        flag_key: str,
        effect: FeatureFlagRuleEffect,
        user_id: str | None = None,
        org_id: str | None = None,
        email_pattern: str | None = None,
        percentage: float | None = None,
        priority: int = 0,
        session: Optional[AsyncSession] = None,
    ) -> FeatureFlagRule:
        """Create a targeting rule for a flag.

        Args:
            flag_key: The flag to attach the rule to.
            effect: INCLUDE or EXCLUDE.
            user_id: Match a specific user id, or None for all.
            org_id: Match a specific org id, or None for all.
            email_pattern: SQL LIKE pattern for email matching (e.g. '%@x.com').
            percentage: 0-100 inclusive rollout bucket threshold, or None.
            priority: Higher priority rules are evaluated first.
            session: Optional shared session.

        Raises:
            ValueError: if the flag does not exist.
        """
        if session is not None:
            flag = await FeatureFlagStore._get_flag(flag_key, session)
            if flag is None:
                raise ValueError(f'Flag with key {flag_key!r} does not exist')
            return await FeatureFlagStore._create_rule(
                flag.id,
                effect,
                user_id,
                org_id,
                email_pattern,
                percentage,
                priority,
                session,
            )
        async with a_session_maker() as new_session:
            flag = await FeatureFlagStore._get_flag(flag_key, new_session)
            if flag is None:
                raise ValueError(f'Flag with key {flag_key!r} does not exist')
            rule = await FeatureFlagStore._create_rule(
                flag.id,
                effect,
                user_id,
                org_id,
                email_pattern,
                percentage,
                priority,
                new_session,
            )
            await new_session.commit()
            return rule

    @staticmethod
    async def _get_matching_rules(
        flag_id: int,
        user_id: str | None,
        org_id: str | None,
        email: str | None,
        session: AsyncSession,
    ) -> list[FeatureFlagRule]:
        """Fetch rules that match the given context dimensions for a flag.

        A rule matches when every populated dimension matches the context:
        - user_id is NULL OR rule.user_id == user_id
        - org_id is NULL OR rule.org_id == org_id
        - email_pattern is NULL OR email LIKE email_pattern (case-insensitive)
        Percentage is not a match filter -- it is applied during evaluation.

        When the context is anonymous (user_id/org_id/email all None), only
        fully-blank rules can match: a populated dimension requires a
        populated context value. This mirrors ``_rule_matches_context`` so the
        cached read-path and this DB pre-filter agree.
        """
        conditions = [FeatureFlagRule.flag_id == flag_id]
        if user_id is None:
            # Anonymous context: only fully-blank rules can match.
            conditions.append(FeatureFlagRule.user_id.is_(None))
            conditions.append(FeatureFlagRule.org_id.is_(None))
            conditions.append(FeatureFlagRule.email_pattern.is_(None))
        else:
            conditions.append(
                or_(
                    FeatureFlagRule.user_id.is_(None),
                    FeatureFlagRule.user_id == user_id,
                )
            )
            if org_id is not None:
                conditions.append(
                    or_(
                        FeatureFlagRule.org_id.is_(None),
                        FeatureFlagRule.org_id == org_id,
                    )
                )
            else:
                # org_id context is None: rule must not target a specific org.
                conditions.append(FeatureFlagRule.org_id.is_(None))
            if email is not None:
                conditions.append(
                    or_(
                        FeatureFlagRule.email_pattern.is_(None),
                        func.lower(email).like(
                            func.lower(FeatureFlagRule.email_pattern)
                        ),
                    )
                )
            else:
                # email context is None: rule must not target an email pattern.
                conditions.append(FeatureFlagRule.email_pattern.is_(None))
        result = await session.execute(
            select(FeatureFlagRule)
            .where(*conditions)
            .order_by(FeatureFlagRule.priority.desc(), FeatureFlagRule.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def _delete_rule(
        rule_id: int,
        session: AsyncSession,
    ) -> bool:
        result = await session.execute(
            select(FeatureFlagRule).where(FeatureFlagRule.id == rule_id)
        )
        rule = result.scalars().first()
        if rule:
            await session.delete(rule)
            await session.flush()
            return True
        return False

    @staticmethod
    async def delete_rule(
        rule_id: int,
        session: Optional[AsyncSession] = None,
    ) -> bool:
        """Delete a rule by id. Returns True if deleted, False if not found."""
        if session is not None:
            return await FeatureFlagStore._delete_rule(rule_id, session)
        async with a_session_maker() as new_session:
            deleted = await FeatureFlagStore._delete_rule(rule_id, new_session)
            if deleted:
                await new_session.commit()
            return deleted
