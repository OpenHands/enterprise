"""Organization Model Routers (SDK meta-profiles) stored on an org row.

A Model Router is a :class:`~openhands.sdk.llm.meta_profile_store.MetaProfile`:
a classifier LLM plus either structured classes or a direct-routing prompt that
pick the saved LLM profile a task should run on. Every model reference in it is
the *name* of an org LLM profile, so the envelope carries no credentials and is
persisted as plain JSON in ``org.meta_profiles``.

Envelope:
``{profiles: {<name>: MetaProfile}, active: <name> | null,
run_at_conversation_start: bool}``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from openhands.app_server.utils.logger import openhands_logger as logger
from openhands.sdk.llm.meta_profile_store import MetaProfile

# Routing fields on ``OpenHandsAgentSettings`` that the org owns. They are set
# only by the conversation-start hydration from the org's active router, so
# stored org settings and member diffs must never carry them.
ORG_OWNED_ROUTER_SETTINGS_KEYS: frozenset[str] = frozenset(
    {
        'enable_classify_and_switch_llm_tool',
        'active_meta_profile',
        'meta_profile',
        'meta_profile_llms',
    }
)


def referenced_llm_profile_names(config: MetaProfile) -> set[str]:
    """Names of the saved LLM profiles a router explicitly names.

    The classifier plus every structured class target. A direct-routing
    ``prompt_template`` may name any saved profile in its free text, which
    cannot be enumerated here.
    """
    return {config.classifier_model, *(item.model for item in config.classes)}


class OrgMetaProfiles(BaseModel):
    """Named collection of Model Routers stored on an org row.

    Invariants (enforced on validate + assignment):
    - Routers that fail to parse (schema drift) are dropped with a warning
      rather than failing the whole load, like :class:`ProviderConnections`.
    - ``active`` always names an existing router, or is ``None``.
    """

    model_config = ConfigDict(validate_assignment=True)

    profiles: dict[str, MetaProfile] = Field(default_factory=dict)
    active: str | None = None
    # Org-wide "Run on first message": when on, every launch that carries the
    # active router is also told to call ``route_task_to_model`` on the first
    # user message (the same instruction Agent Canvas emits locally).
    run_at_conversation_start: bool = False

    @field_validator('profiles', mode='before')
    @classmethod
    def _skip_invalid_profiles(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        valid: dict[str, Any] = {}
        for name, raw in value.items():
            if isinstance(raw, MetaProfile):
                valid[name] = raw
                continue
            try:
                valid[name] = MetaProfile.model_validate(raw)
            except ValidationError as exc:
                logger.warning('Skipping invalid org Model Router %r: %s', name, exc)
        return valid

    def model_post_init(self, __context: Any) -> None:
        if self.active is not None and self.active not in self.profiles:
            logger.warning(
                'Active org Model Router %r is not stored; clearing the pointer',
                self.active,
            )
            self.active = None

    # ── Queries ────────────────────────────────────────────────────

    def get(self, name: str) -> MetaProfile | None:
        return self.profiles.get(name)

    def active_profile(self) -> tuple[str, MetaProfile] | None:
        """Return ``(name, config)`` of the active router, or ``None``."""
        if self.active is None:
            return None
        config = self.profiles.get(self.active)
        if config is None:
            return None
        return self.active, config

    def llm_profile_referrers(self, llm_profile_name: str) -> list[str]:
        """Names of routers whose classifier or class targets name ``llm_profile_name``."""
        return sorted(
            name
            for name, config in self.profiles.items()
            if llm_profile_name in referenced_llm_profile_names(config)
        )

    def summaries(self) -> list[dict[str, Any]]:
        """Return a ``{name, classifier_model, num_classes}`` dict per router, sorted."""
        return [
            {
                'name': name,
                'classifier_model': config.classifier_model,
                'num_classes': len(config.classes),
            }
            for name, config in sorted(self.profiles.items())
        ]
