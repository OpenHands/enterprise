"""Store for managing verified LLM models in the database."""

from dataclasses import dataclass
from datetime import datetime

from server.verified_models.verified_model_models import (
    VerifiedModel,
    VerifiedModelPage,
)
from sqlalchemy import (
    DateTime,
    Identity,
    String,
    UniqueConstraint,
    and_,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from storage.base import Base

from openhands.app_server.services.db_session import depends_db_session
from openhands.app_server.utils.logger import openhands_logger as logger


class StoredVerifiedModel(Base):
    """A verified LLM model available in the model selector.

    The composite unique constraint on (model_name, provider) allows the same
    model name to exist under different providers (e.g. 'claude-sonnet' under
    both 'openhands' and 'anthropic').
    """

    __tablename__ = 'verified_models'
    __table_args__ = (
        UniqueConstraint('model_name', 'provider', name='uq_verified_model_provider'),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    is_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text('true')
    )
    is_free: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text('false')
    )
    is_default: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text('false')
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


def verified_model(result: StoredVerifiedModel) -> VerifiedModel:
    return VerifiedModel(
        id=result.id,
        model_name=result.model_name,
        provider=result.provider,
        is_enabled=result.is_enabled,
        is_free=result.is_free,
        is_default=result.is_default,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


@dataclass
class VerifiedModelService:
    """Store for CRUD operations on verified models.

    Follows the async pattern with db_session as an attribute.
    """

    db_session: AsyncSession

    async def search_verified_models(
        self,
        provider: str | None = None,
        enabled_only: bool = True,
        page_id: str | None = None,
        limit: int = 100,
    ) -> VerifiedModelPage:
        """Search for verified models with optional filtering and pagination.

        Args:
            provider: Optional provider name to filter by (e.g., 'openhands', 'anthropic')
            enabled_only: If True, only return enabled models (default: True)
            page_id: Page id for pagination
            limit: Maximum number of records to return

        Returns:
            SearchModelsResult containing items list and has_more flag
        """
        query = select(StoredVerifiedModel)

        # Build filters
        filters = []
        if provider:
            filters.append(StoredVerifiedModel.provider == provider)
        if enabled_only:
            filters.append(StoredVerifiedModel.is_enabled.is_(True))

        if filters:
            query = query.where(and_(*filters))

        # Order by provider, then model_name
        query = query.order_by(
            StoredVerifiedModel.provider, StoredVerifiedModel.model_name
        )

        # Fetch limit + 1 to check if there are more results
        offset = int(page_id or '0')
        query = query.offset(offset).limit(limit + 1)

        result = await self.db_session.execute(query)
        results = list(result.scalars().all())
        has_more = len(results) > limit
        next_page_id = None

        # Return only the requested number of results
        if has_more:
            next_page_id = str(offset + limit)
            results.pop()

        items = [verified_model(result) for result in results]
        return VerifiedModelPage(items=items, next_page_id=next_page_id)

    async def get_model(self, model_name: str, provider: str) -> VerifiedModel | None:
        """Get a model by its composite key (model_name, provider).

        Args:
            model_name: The model identifier
            provider: The provider name
        """
        query = select(StoredVerifiedModel).where(
            and_(
                StoredVerifiedModel.model_name == model_name,
                StoredVerifiedModel.provider == provider,
            )
        )
        result = await self.db_session.execute(query)
        stored = result.scalars().first()
        return verified_model(stored) if stored else None

    async def _clear_default_for_provider(
        self, provider: str, except_id: int | None = None
    ) -> None:
        """Unset ``is_default`` on every other row of the same provider.

        Only one model per provider may be the default. A partial unique index
        enforces this at the DB level; clearing here keeps the write ordering
        correct so the new default never collides with a stale one.
        """
        conditions = [
            StoredVerifiedModel.provider == provider,
            StoredVerifiedModel.is_default.is_(True),
        ]
        if except_id is not None:
            conditions.append(StoredVerifiedModel.id != except_id)
        result = await self.db_session.execute(
            select(StoredVerifiedModel).where(and_(*conditions))
        )
        for row in result.scalars().all():
            row.is_default = False

    async def create_verified_model(
        self,
        model_name: str,
        provider: str,
        is_enabled: bool = True,
        is_free: bool = False,
        is_default: bool = False,
    ) -> VerifiedModel:
        """Create a new verified model.

        Args:
            model_name: The model identifier
            provider: The provider name
            is_enabled: Whether the model is enabled (default True)
            is_free: Whether the model is free on the OpenHands provider
            is_default: Whether the model is the provider's default. Setting
                this clears any existing default for the same provider.

        Raises:
            ValueError: If a model with the same (model_name, provider) already exists
        """
        existing_query = select(StoredVerifiedModel).where(
            and_(
                StoredVerifiedModel.model_name == model_name,
                StoredVerifiedModel.provider == provider,
            )
        )
        result = await self.db_session.execute(existing_query)
        existing = result.scalars().first()
        if existing:
            raise ValueError(f'Model {provider}/{model_name} already exists')

        if is_default:
            await self._clear_default_for_provider(provider)

        model = StoredVerifiedModel(
            model_name=model_name,
            provider=provider,
            is_enabled=is_enabled,
            is_free=is_free,
            is_default=is_default,
        )
        self.db_session.add(model)
        await self.db_session.commit()
        await self.db_session.refresh(model)
        logger.info(f'Created verified model: {provider}/{model_name}')
        return verified_model(model)

    async def update_verified_model(
        self,
        model_name: str,
        provider: str,
        is_enabled: bool | None = None,
        is_free: bool | None = None,
        is_default: bool | None = None,
    ) -> VerifiedModel | None:
        """Update an existing verified model.

        Args:
            model_name: The model name to update
            provider: The provider name
            is_enabled: New enabled state (optional)
            is_free: New free state (optional)
            is_default: New default state (optional). Setting it to ``True``
                clears any existing default for the same provider.

        Returns:
            The updated model if found, None otherwise
        """
        query = select(StoredVerifiedModel).where(
            and_(
                StoredVerifiedModel.model_name == model_name,
                StoredVerifiedModel.provider == provider,
            )
        )
        result = await self.db_session.execute(query)
        model = result.scalars().first()
        if not model:
            return None

        if is_enabled is not None:
            model.is_enabled = is_enabled
        if is_free is not None:
            model.is_free = is_free
        if is_default is not None:
            if is_default:
                await self._clear_default_for_provider(provider, except_id=model.id)
            model.is_default = is_default

        await self.db_session.commit()
        await self.db_session.refresh(model)
        logger.info(f'Updated verified model: {provider}/{model_name}')
        return verified_model(model)

    async def delete_verified_model(self, model_name: str, provider: str):
        """Delete a verified model.

        Args:
            model_name: The model name to delete
            provider: The provider name

        Returns:
            True if deleted, False if not found
        """
        query = select(StoredVerifiedModel).where(
            and_(
                StoredVerifiedModel.model_name == model_name,
                StoredVerifiedModel.provider == provider,
            )
        )
        result = await self.db_session.execute(query)
        model = result.scalars().first()
        if not model:
            raise ValueError('Unknown model')

        await self.db_session.delete(model)
        await self.db_session.commit()
        logger.info(f'Deleted verified model: {provider}/{model_name}')


def verified_model_store_dependency(db_session: AsyncSession = depends_db_session()):
    return VerifiedModelService(db_session)
