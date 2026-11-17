from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from storage.base import Base


class StoredRepository(Base):
    """
    Represents a repositories fetched from git providers.
    """

    __tablename__ = 'repos'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    repo_name: Mapped[str] = mapped_column(String, nullable=False)
    repo_id: Mapped[str] = mapped_column(
        String, nullable=False
    )  # {provider}##{id} format
    is_public: Mapped[bool] = mapped_column(nullable=False)
    has_microagent: Mapped[bool | None] = mapped_column(nullable=True)
    has_setup_script: Mapped[bool | None] = mapped_column(nullable=True)
    # The auth provider (GitLab instance) the ids belong to; NULL when unknown.
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey('auth_providers.id', ondelete='CASCADE'), nullable=True
    )
