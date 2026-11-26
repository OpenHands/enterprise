"""Pure operations on JSON settings. Only edit() interprets write intent."""

from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


@dataclass(frozen=True)
class Set:
    path: tuple[str, ...]
    value: Any


@dataclass(frozen=True)
class Inherit:
    path: tuple[str, ...]


Operation = Set | Inherit


class SetRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['set']
    path: tuple[str, ...]
    value: JsonValue


class InheritRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['inherit']
    path: tuple[str, ...]


OperationRequest = Annotated[SetRequest | InheritRequest, Field(discriminator='op')]


def resolve(*layers: dict[str, Any]) -> dict[str, Any]:
    """Overlay objects recursively; arrays, scalars and literal null are atomic."""
    result: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            prior = result.get(key)
            result[key] = (
                resolve(prior, value)
                if isinstance(prior, dict) and isinstance(value, dict)
                else deepcopy(value)
            )
    return result


def edit(local: dict[str, Any], operations: Iterable[Operation]) -> dict[str, Any]:
    """Apply ordered operations to this scope, never to its resolved view."""
    result = deepcopy(local)
    for op in operations:
        if not op.path:
            raise ValueError('A settings operation must name a path')
        node = result
        parents = []
        for key in op.path[:-1]:
            child = node.get(key)
            if not isinstance(child, dict):
                if isinstance(op, Inherit):
                    break  # Nothing local to remove under this path.
                node[key] = child = {}
            parents.append((node, key))
            node = child
        else:
            leaf = op.path[-1]
            if isinstance(op, Set):
                node[leaf] = deepcopy(op.value)
            elif leaf in node:
                del node[leaf]
                for parent, key in reversed(parents):
                    if node:
                        break
                    del parent[key]
                    node = parent
    return result
