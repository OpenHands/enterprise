"""Tests for the declarative SearchFilter utilities.

These tests exercise both the in-memory ``matches`` path and the
``filter_sql`` path against a real (in-memory SQLite) SQLAlchemy database so
that the generated ``WHERE`` clauses are actually executed, not just
string-compared.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import DateTime, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from server.utils.search_filter import BaseSearchFilter, SearchFilter


class _Base(DeclarativeBase):
    pass


class _User(_Base):
    __tablename__ = "user"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    age: Mapped[int | None] = mapped_column(nullable=True)


class _UserSearchFilter(BaseSearchFilter, entity=_User):
    email__contains: str | None = None
    email__eq: str | None = None
    age__lt: int | None = None
    age__lte: int | None = None
    age__gt: int | None = None
    age__gte: int | None = None


@pytest.fixture()
def session():
    """Create a fresh in-memory SQLite session for each test."""
    engine = create_engine("sqlite://", echo=False)
    _Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s


def _seed(session) -> list[_User]:
    users = [
        _User(
            email="alice@example.com",
            created_at=datetime(2024, 1, 1),
            age=30,
        ),
        _User(
            email="bob@example.com",
            created_at=datetime(2020, 1, 1),
            age=25,
        ),
        _User(
            email="charlie@other.io",
            created_at=datetime(2025, 6, 1),
            age=40,
        ),
        _User(email=None, created_at=None, age=None),
    ]
    session.add_all(users)
    session.commit()
    return users


class TestSearchFilterBase:
    """Tests for the abstract :class:`SearchFilter` base."""

    def test_search_filter_is_abstract(self):
        """The base SearchFilter cannot be instantiated directly."""
        with pytest.raises(TypeError):
            SearchFilter()  # type: ignore[abstract]

    def test_entity_resolution_requires_entity(self):
        """A subclass without an entity raises a clear error."""

        class _NoEntity(BaseSearchFilter):
            pass

        with pytest.raises(NotImplementedError):
            _NoEntity().entity()

    def test_entity_kwarg_sets_entity(self):
        """Passing entity= as a class kwarg binds the entity."""
        assert _UserSearchFilter.entity() is _User


class TestMatches:
    """Tests for the in-memory ``matches`` path."""

    def test_empty_filter_matches_everything(self, session):
        users = _seed(session)
        f = _UserSearchFilter()
        assert all(f.matches(u) for u in users)

    def test_contains_matches_substring(self, session):
        users = _seed(session)
        f = _UserSearchFilter(email__contains="example.com")
        assert f.matches(users[0])  # alice@example.com
        assert not f.matches(users[2])  # charlie@other.io
        assert not f.matches(users[3])  # None email

    def test_eq_matches_exact(self, session):
        users = _seed(session)
        f = _UserSearchFilter(email__eq="alice@example.com")
        assert f.matches(users[0])
        assert not f.matches(users[1])

    def test_lt_strict(self, session):
        users = _seed(session)
        f = _UserSearchFilter(age__lt=30)
        assert f.matches(users[1])  # 25
        assert not f.matches(users[0])  # 30 (not strictly less)
        assert not f.matches(users[3])  # None

    def test_lte_inclusive(self, session):
        users = _seed(session)
        f = _UserSearchFilter(age__lte=30)
        assert f.matches(users[0])  # 30
        assert f.matches(users[1])  # 25
        assert not f.matches(users[2])  # 40

    def test_gt_strict(self, session):
        users = _seed(session)
        f = _UserSearchFilter(age__gt=30)
        assert f.matches(users[2])  # 40
        assert not f.matches(users[0])  # 30 (not strictly greater)

    def test_gte_inclusive(self, session):
        users = _seed(session)
        f = _UserSearchFilter(age__gte=30)
        assert f.matches(users[0])  # 30
        assert f.matches(users[2])  # 40
        assert not f.matches(users[1])  # 25

    def test_multiple_clauses_are_anded(self, session):
        users = _seed(session)
        f = _UserSearchFilter(email__contains="example.com", age__gte=30)
        assert f.matches(users[0])  # alice, 30
        assert not f.matches(users[1])  # bob, 25 (age too low)
        assert not f.matches(users[2])  # charlie, no example.com


class TestFilterSql:
    """Tests for the SQLAlchemy ``filter_sql`` path."""

    def test_empty_filter_returns_query_unchanged(self, session):
        _seed(session)
        f = _UserSearchFilter()
        base = select(_User)
        filtered = f.filter_sql(base)
        rows = list(session.execute(filtered).scalars())
        assert len(rows) == 4

    def test_contains_filters_rows(self, session):
        _seed(session)
        f = _UserSearchFilter(email__contains="example.com")
        rows = list(session.execute(f.filter_sql(select(_User))).scalars())
        emails = {r.email for r in rows}
        assert emails == {"alice@example.com", "bob@example.com"}

    def test_eq_filters_rows(self, session):
        _seed(session)
        f = _UserSearchFilter(email__eq="alice@example.com")
        rows = list(session.execute(f.filter_sql(select(_User))).scalars())
        assert len(rows) == 1
        assert rows[0].email == "alice@example.com"

    def test_lt_filters_rows(self, session):
        _seed(session)
        f = _UserSearchFilter(age__lt=30)
        rows = list(session.execute(f.filter_sql(select(_User))).scalars())
        ages = {r.age for r in rows}
        assert ages == {25}

    def test_lte_filters_rows(self, session):
        _seed(session)
        f = _UserSearchFilter(age__lte=30)
        rows = list(session.execute(f.filter_sql(select(_User))).scalars())
        ages = {r.age for r in rows}
        assert ages == {25, 30}

    def test_gt_filters_rows(self, session):
        _seed(session)
        f = _UserSearchFilter(age__gt=30)
        rows = list(session.execute(f.filter_sql(select(_User))).scalars())
        ages = {r.age for r in rows}
        assert ages == {40}

    def test_gte_filters_rows(self, session):
        _seed(session)
        f = _UserSearchFilter(age__gte=30)
        rows = list(session.execute(f.filter_sql(select(_User))).scalars())
        ages = {r.age for r in rows}
        assert ages == {30, 40}

    def test_multiple_clauses_are_anded_in_sql(self, session):
        _seed(session)
        f = _UserSearchFilter(email__contains="example.com", age__gte=30)
        rows = list(session.execute(f.filter_sql(select(_User))).scalars())
        assert len(rows) == 1
        assert rows[0].email == "alice@example.com"

    def test_does_not_mutate_caller_query(self, session):
        """filter_sql must return a new statement, leaving the input alone."""
        _seed(session)
        base = select(_User)
        f = _UserSearchFilter(email__contains="alice")
        _ = f.filter_sql(base)
        # The original query has no WHERE clause.
        rows = list(session.execute(base).scalars())
        assert len(rows) == 4


class TestSerialization:
    """Tests for the DiscriminatedUnionMixin round-trip."""

    def test_roundtrip_with_kind_discriminator(self):
        f = _UserSearchFilter(email__contains="alice", age__gte=30)
        data = f.model_dump()
        assert data["kind"] == "_UserSearchFilter"
        restored = _UserSearchFilter.model_validate(data)
        assert restored.email__contains == "alice"
        assert restored.age__gte == 30

    def test_none_clauses_excluded_from_serialization_when_unset(self):
        f = _UserSearchFilter(email__contains="alice")
        data = f.model_dump()
        # Only the set clause carries a value; unset ones are None.
        assert data["email__contains"] == "alice"
        assert data["age__lt"] is None


class TestParsingAndErrors:
    """Tests for attribute parsing and error handling."""

    def test_non_filter_fields_are_ignored(self):
        """Plain attributes without __ are not treated as filter clauses."""

        class _FilterWithMeta(BaseSearchFilter, entity=_User):
            email__contains: str | None = None
            meta_field: str = "constant"

        f = _FilterWithMeta(email__contains="alice")
        # meta_field is not a filter clause, so it does not break matches.
        u = _User(email="alice@example.com", age=10)
        assert f.matches(u)

    def test_unknown_operator_raises(self):
        class _BadFilter(BaseSearchFilter, entity=_User):
            email__bogus: str | None = None

        f = _BadFilter(email__bogus="x")
        with pytest.raises(ValueError, match="Unsupported filter operator"):
            f.matches(_User(email="x"))

    def test_missing_column_raises(self):
        class _BadFilter(BaseSearchFilter, entity=_User):
            nonexistent__eq: str | None = None

        f = _BadFilter(nonexistent__eq="x")
        # The column is only resolved when building a SQL clause.
        with pytest.raises(AttributeError, match="has no attribute"):
            f.filter_sql(select(_User))
