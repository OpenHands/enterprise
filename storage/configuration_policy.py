from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ConfigurationOperation(StrEnum):
    LOCKED = 'locked'
    REPLACE = 'replace'
    SUBSET = 'subset'
    EXTEND = 'extend'


class ConfigurationRule(BaseModel):
    operation: ConfigurationOperation = ConfigurationOperation.EXTEND


class ConfigurationPolicy(BaseModel):
    llm: ConfigurationRule = Field(default_factory=ConfigurationRule)


def load_configuration_policy(value: dict[str, Any] | None) -> ConfigurationPolicy:
    try:
        return ConfigurationPolicy.model_validate(value or {})
    except (TypeError, ValueError):
        return ConfigurationPolicy()
