"""Reusable, declarative search filters.

A :class:`SearchFilter` is a pydantic model (via
:class:`openhands.sdk.utils.models.DiscriminatedUnionMixin`) that is generic
over an entity type and knows two things:

* ``matches(item)`` -- whether a single in-memory object satisfies the filter.
* ``filter_sql(query)`` -- how to restrict a SQLAlchemy ``Select`` to matching
  rows.

Concrete subclasses are normally written by extending
:class:`BaseSearchFilter`, which reads the declared field names and turns a
declarative ``field__op`` naming convention into matching + SQL behaviour
automatically.  For example::

    class UserSearchFilter(BaseSearchFilter, entity=User):
        email__contains: str | None = None
        created_at__gte: datetime | None = None

declares "match users whose ``email`` *contains* ``email__contains`` and whose
``created_at`` is *greater than or equal to* ``created_at__gte``".

Supported operators (the suffix after the ``__`` separator)::

    contains -- substring match (case-sensitive)
    eq       -- equality
    lt       -- strictly less than
    lte      -- less than or equal to
    gt       -- strictly greater than
    gte      -- greater than or equal to

Filters are designed to be optional inclusions on search endpoints and, when
attached to a permission, to limit that permission to a subset of items within
a resource type.  Because every filter is a :class:`DiscriminatedUnionMixin`,
subclasses round-trip through serialization with a ``kind`` discriminator and
can be mixed into larger discriminated-union shapes (e.g. a permission that
carries an optional filter).
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from sqlalchemy import ColumnElement, Select
from sqlalchemy.orm import DeclarativeBase

from openhands.sdk.utils.models import DiscriminatedUnionMixin

EntityT = TypeVar("EntityT", bound=DeclarativeBase)


# ---------------------------------------------------------------------------
# Operator registry
# ---------------------------------------------------------------------------
#
# Each entry maps the suffix used in a ``field__op`` attribute name to:
#   * the Python comparison callable used by ``matches`` (left, right) -> bool
#   * the SQLAlchemy column-builder callable used by ``filter_sql`` (col, value)
#
# Keeping both in one place means ``BaseSearchFilter`` only has to look the
# operator up once to satisfy both the in-memory and SQL contracts.
_OPERATORS: dict[str, tuple[Any, Any]] = {
    "contains": (
        lambda field, value: value in field if field is not None else False,
        lambda col, value: col.ilike(f"%{value}%"),
    ),
    "eq": (
        lambda field, value: field == value,
        lambda col, value: col == value,
    ),
    "lt": (
        lambda field, value: field is not None and field < value,
        lambda col, value: col < value,
    ),
    "lte": (
        lambda field, value: field is not None and field <= value,
        lambda col, value: col <= value,
    ),
    "gt": (
        lambda field, value: field is not None and field > value,
        lambda col, value: col > value,
    ),
    "gte": (
        lambda field, value: field is not None and field >= value,
        lambda col, value: col >= value,
    ),
}


class SearchFilter(DiscriminatedUnionMixin, Generic[EntityT]):
    """Abstract base class for declarative entity search filters.

    A ``SearchFilter`` is generic over the SQLAlchemy entity it applies to.
    Subclasses declare the concrete entity by passing ``entity=MyModel`` as a
    class creation keyword (e.g.
    ``class UserFilter(BaseSearchFilter, entity=User):``) or by setting
    ``__entity__`` directly on the subclass, and implement :meth:`matches`
    and :meth:`filter_sql`.

    Because the class mixes in :class:`DiscriminatedUnionMixin`, every
    concrete subclass serializes with a ``kind`` discriminator equal to the
    class name, so filters can be persisted, transported, and rehydrated as
    part of a larger discriminated-union shape (for example, a permission that
    carries an optional filter limiting it to a subset of a resource type).
    """

    __entity__: ClassVar[type[DeclarativeBase] | None] = None

    def __init_subclass__(
        cls, entity: type[DeclarativeBase] | None = None, **kwargs: Any
    ) -> None:
        # Pop our own ``entity`` keyword before delegating: the upstream
        # ``Generic.__init_subclass__`` rejects unknown kwargs, so it must be
        # consumed here rather than passed through.
        if entity is not None:
            cls.__entity__ = entity
        super().__init_subclass__(**kwargs)

    @abstractmethod
    def matches(self, item: EntityT) -> bool:
        """Return whether ``item`` satisfies this filter.

        Args:
            item: An instance of the entity type this filter applies to.

        Returns:
            ``True`` if every clause in the filter holds for ``item``,
            ``False`` otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def filter_sql(self, query: Select[tuple[EntityT]]) -> Select[tuple[EntityT]]:
        """Return ``query`` restricted to rows matching this filter.

        Implementations should add ``WHERE`` predicates to the supplied
        ``Select`` and return the new statement; they must not mutate the
        caller's statement in place.

        Args:
            query: A SQLAlchemy ``Select`` over the entity type.

        Returns:
            A new ``Select`` that yields only rows satisfying the filter.
        """
        raise NotImplementedError

    # -- helpers for concrete subclasses -----------------------------------

    @classmethod
    def entity(cls) -> type[DeclarativeBase]:
        """Return the SQLAlchemy entity this filter applies to."""
        entity = cls.__entity__
        if entity is None:
            raise NotImplementedError(
                f"{cls.__name__} does not declare an entity; "
                "set the ``entity`` class argument or ``__entity__``."
            )
        return entity


class BaseSearchFilter(SearchFilter[EntityT]):
    """Mixin that derives ``matches``/``filter_sql`` from declared fields.

    Subclasses declare optional filter clauses using the
    ``<field>__<operator>`` naming convention, where ``<field>`` is the name of
    an attribute on the bound SQLAlchemy entity and ``<operator>`` is one of
    the supported operators (see :data:`_OPERATORS`).  Example::

        class UserSearchFilter(BaseSearchFilter, entity=User):
            email__contains: str | None = None
            created_at__gte: datetime | None = None

    Only clauses whose value is not ``None`` are applied; ``None`` means
    "no constraint on this field".  An instance with every clause set to
    ``None`` matches everything and adds no ``WHERE`` predicate.
    """

    def matches(self, item: EntityT) -> bool:
        for field_name, op, value in self._active_clauses():
            actual = getattr(item, field_name, None)
            compare = _OPERATORS[op][0]
            if not compare(actual, value):
                return False
        return True

    def filter_sql(self, query: Select[tuple[EntityT]]) -> Select[tuple[EntityT]]:
        clauses = [
            self._build_clause(field_name, op, value)
            for field_name, op, value in self._active_clauses()
        ]
        if not clauses:
            return query
        return query.where(*clauses)  # type: ignore[arg-type]

    # -- internals ---------------------------------------------------------

    def _active_clauses(self) -> list[tuple[str, str, Any]]:
        """Yield ``(field_name, operator, value)`` for every set clause.

        A clause is "active" when its declared value is not ``None``; clauses
        left at their default of ``None`` impose no constraint.  Attribute
        names that do not follow the ``field__op`` convention are ignored, so
        subclasses can freely carry non-filter pydantic fields alongside the
        filter clauses.
        """
        active: list[tuple[str, str, Any]] = []
        for attr_name in type(self).model_fields:
            value = getattr(self, attr_name)
            if value is None:
                continue
            field_name, op = self._parse_attr(attr_name)
            if op is None:
                continue
            if op not in _OPERATORS:
                raise ValueError(
                    f"Unsupported filter operator ``{op}`` on "
                    f"{type(self).__name__}.{attr_name}; "
                    f"expected one of {sorted(_OPERATORS)}."
                )
            active.append((field_name, op, value))
        return active

    @staticmethod
    def _parse_attr(attr_name: str) -> tuple[str, str | None]:
        """Split ``field__op`` into ``(field, op)``.

        Returns ``(field, None)`` when the attribute does not use the
        ``__`` separator, so non-filter fields are skipped harmlessly.
        """
        if "__" not in attr_name:
            return attr_name, None
        field, _, op = attr_name.rpartition("__")
        return field, op or None

    def _build_clause(
        self, field_name: str, op: str, value: Any
    ) -> ColumnElement[bool]:
        """Build the SQLAlchemy ``WHERE`` clause for a single active clause."""
        column = self._resolve_column(field_name)
        builder = _OPERATORS[op][1]
        return builder(column, value)

    def _resolve_column(self, field_name: str) -> Any:
        """Resolve the SQLAlchemy column for ``field_name`` on the entity.

        Returns the instrumented ``InstrumentedAttribute`` from the bound
        entity class, which is what SQLAlchemy ``WHERE`` expressions expect.
        """
        entity = self.entity()
        column = getattr(entity, field_name, None)
        if column is None:
            raise AttributeError(
                f"{entity.__name__} has no attribute ``{field_name}``; "
                f"cannot build filter clause."
            )
        return column


# Re-export the public helper so callers can build ad-hoc predicates when a
# custom operator is needed without subclassing.
def build_clause(field_name: str, op: str, value: Any, entity: type[Any]) -> Any:
    """Build a single SQLAlchemy ``WHERE`` clause for ``op`` on ``entity``.

    Convenience wrapper around the operator registry for code that needs to
    construct a predicate without going through a :class:`BaseSearchFilter`
    instance.
    """
    if op not in _OPERATORS:
        raise ValueError(
            f"Unsupported filter operator ``{op}``; expected one of "
            f"{sorted(_OPERATORS)}."
        )
    column = getattr(entity, field_name, None)
    if column is None:
        raise AttributeError(f"{entity.__name__} has no attribute ``{field_name}``.")
    return _OPERATORS[op][1](column, value)
