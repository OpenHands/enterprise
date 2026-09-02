"""Feature flag evaluation service.

Determines whether a flag is enabled for a given context, applying the
precedence: global switch -> exclude rules -> include rules -> percentage
rollout -> default. This mirrors the "whitelist beats blacklist" precedence of
``default_user_authorizer`` generalized to include/exclude + targeting.

Env-var fallback (standard pattern): a flag key registered in
``_ENV_FLAG_DEFAULTS`` (via ``register_env_default``) resolves to its
environment variable when no database row exists; once a row exists the
database is authoritative. This is the standard way to migrate an env-var
toggle (e.g. ``ENABLE_BILLING``) to a DB-managed feature flag. Sync or
import-time callers that cannot await ``is_enabled`` can read the same
fallback via ``FeatureFlagService.resolve_env_default``.
"""

import hashlib
import logging
import os
from dataclasses import dataclass

from storage.feature_flag import FeatureFlag, FeatureFlagRule, FeatureFlagRuleEffect
from storage.feature_flag_store import FeatureFlagStore

logger = logging.getLogger(__name__)

# Cache TTL (seconds) for the per-flag read-path. A short in-memory cache keeps
# hot ``is_enabled`` checks off the database without making flag updates take
# minutes to propagate.
_DEFAULT_CACHE_TTL_SECONDS = 5

# Cache TTL (seconds) for the global-flags snapshot used by unauthenticated
# paths (e.g. the web-client config endpoint). Longer than the per-flag TTL
# because the global set is only the rule-less flags -- it changes rarely, the
# endpoint is called on every page load, and admin mutations call
# ``invalidate`` to drop the snapshot immediately. This caps the DB load at
# roughly one refresh per minute per worker process.
_DEFAULT_GLOBAL_CACHE_TTL_SECONDS = 60


@dataclass
class _EnvFlagDefault:
    """Env-var fallback for a flag that has no database row yet.

    When a flag key has no ``FeatureFlag`` row, its value is resolved from the
    environment variable ``env_var`` (defaulting to ``default_bool``). This
    lets an operator toggle a known flag (e.g. ``ENABLE_BILLING``) via env
    before/without promoting it to a DB-managed flag. Once a DB row exists the
    database is authoritative and the env fallback is ignored.
    """

    env_var: str
    default_bool: bool


# Known flag keys that fall back to an environment variable when they have no
# database row. Each entry maps flag_key -> (env_var_name, default_value). To
# register a new env-backed flag, add it here (or call
# ``FeatureFlagService.register_env_default`` at import time). Keep this the
# minimal set of flags that already have an env-var contract; arbitrary flag
# keys default to False when missing and unmapped.
_ENV_FLAG_DEFAULTS: dict[str, _EnvFlagDefault] = {
    'ENABLE_BILLING': _EnvFlagDefault('ENABLE_BILLING', False),
}


@dataclass
class _CacheEntry:
    flag: FeatureFlag | None
    rules: list[FeatureFlagRule]
    fetched_at: float


@dataclass
class _GlobalCacheEntry:
    """Cached snapshot of the global (rule-less) flags -> enabled map."""

    value: dict[str, bool]
    fetched_at: float


class FeatureFlagService:
    """Evaluates feature flags for a request context.

    The cache is process-local and time-based. The first miss for a flag key
    loads the flag + its matching rules from the DB; subsequent evaluations
    within the TTL reuse that snapshot. ``invalidate`` clears the cache (used
    by the admin REST endpoints after a mutation).
    """

    _cache: dict[str, _CacheEntry] = {}
    _global_cache: dict[str, _GlobalCacheEntry] = {}

    @classmethod
    def register_env_default(cls, key: str, env_var: str, default_bool: bool) -> None:
        """Register an env-var fallback for a flag key with no DB row.

        Lets callers (e.g. enterprise config bootstrap) declare additional
        env-backed flags beyond the built-in ``_ENV_FLAG_DEFAULTS``. Replaces
        any existing registration for ``key``. This is global state by design
        -- it is meant to be set once at process startup, before any request
        is served.
        """
        _ENV_FLAG_DEFAULTS[key] = _EnvFlagDefault(env_var, default_bool)

    @classmethod
    def resolve_env_default(cls, key: str) -> bool:
        """Resolve a flag's env-var fallback without touching the database.

        This is the synchronous half of the env-fallback pattern: callers that
        cannot await ``is_enabled`` (module import time, sync helpers) read the
        same env var with the same truthiness rules. Returns ``False`` for
        unregistered keys so the absence of a DB row never implicitly grants
        anything.
        """
        return _env_fallback(key)

    def __init__(
        self,
        cache_ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS,
        global_cache_ttl_seconds: int = _DEFAULT_GLOBAL_CACHE_TTL_SECONDS,
    ) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._global_cache_ttl = global_cache_ttl_seconds

    def invalidate(self, key: str | None = None) -> None:
        """Drop a single flag (or all flags) from the cache."""
        if key is None:
            self._cache.clear()
            self._global_cache.clear()
        else:
            self._cache.pop(key, None)
            # A per-flag change may move it in/out of the global set, so drop
            # the global snapshot too; it is cheap to rebuild.
            self._global_cache.pop('snapshot', None)

    async def is_enabled(
        self,
        key: str,
        user_id: str | None = None,
        org_id: str | None = None,
        email: str | None = None,
    ) -> bool:
        """Evaluate whether ``key`` is on for the given context.

        Precedence:
        1. Flag missing -> env-var fallback if registered, else False. (Once a
           DB row exists the database is authoritative.)
        2. Flag globally disabled -> False.
        3. Any matching EXCLUDE rule -> False.
        4. Any matching INCLUDE rule -> True.
        5. A matching INCLUDE rule with a percentage bucket -> deterministic
           hash check; True if in bucket.
        6. Otherwise the flag's global ``enabled`` state (True if on with no
           rules at all; False if rules exist but none matched).
        """
        flag, all_rules = await self._load(key)
        if flag is None:
            # No DB row: fall back to the env-var default (allow-all / deny-all)
            # for registered keys; unknown keys stay off.
            return _env_fallback(key)
        if not flag.enabled:
            return False

        matching = [
            r for r in all_rules if _rule_matches_context(r, user_id, org_id, email)
        ]
        for rule in matching:
            if rule.effect == FeatureFlagRuleEffect.EXCLUDE.value:
                return False

        has_include = any(
            r.effect == FeatureFlagRuleEffect.INCLUDE.value for r in all_rules
        )
        for rule in matching:
            if rule.effect == FeatureFlagRuleEffect.INCLUDE.value:
                if rule.percentage is not None and user_id is not None:
                    if _in_percentage_bucket(key, user_id, rule.percentage):
                        return True
                    # In bucket-fail: this rule does not grant; continue.
                    continue
                # No user_id, or no percentage on this rule: the bucket check
                # is skipped and the include applies directly. This branch is
                # only reachable for rules whose targeting dimensions are all
                # NULL (a targeted rule cannot match an anonymous context).
                return True

        # No include rule granted the flag. If there are no include rules at
        # all, only excludes were in play and none matched, so the flag falls
        # back to its global enabled state. If include rules exist but none
        # matched, the flag is gated off for this context.
        if not has_include:
            return True
        return False

    async def _load(self, key: str) -> tuple[FeatureFlag | None, list[FeatureFlagRule]]:
        import time

        now = time.monotonic()
        entry = self._cache.get(key)
        if entry is not None and (now - entry.fetched_at) < self._cache_ttl:
            return entry.flag, list(entry.rules)

        flag = await FeatureFlagStore.get_flag(key)
        if flag is None:
            self._cache[key] = _CacheEntry(None, [], now)
            return None, []

        all_rules = await FeatureFlagStore.list_rules(key)
        self._cache[key] = _CacheEntry(flag, all_rules, now)
        return flag, list(all_rules)

    async def get_global_flags(self) -> dict[str, bool]:
        """Return the subset of flags that are safe for an anonymous context.

        Only flags with **no rules at all** are returned: their value is the
        flag's global ``enabled`` switch, independent of any user/org/email
        targeting. Flags that carry rules (include or exclude) are inherently
        context-dependent and must NOT be exposed to unauthenticated callers
        such as the web-client config endpoint -- per-user excludes would
        otherwise leak "who is excluded" and per-user includes would leak
        targeting state.

        Env-var-backed flags (see ``_ENV_FLAG_DEFAULTS``) that have NO database
        row are also included, resolved from their env var, since they are
        inherently global (allow-all / deny-all). This lets the web-client
        config endpoint surface flags like ``ENABLE_BILLING`` before an admin
        promotes them to a DB-managed flag.

        The result is cached for a longer TTL than ``is_enabled`` (default 60s)
        because the global set changes rarely and the web-client config
        endpoint is hit on every page load; admin mutations call
        ``invalidate`` to drop the snapshot immediately. Callers that need the
        per-user value for a targeted flag must use ``is_enabled`` with an
        explicit context.
        """
        import time

        now = time.monotonic()
        cached = self._global_cache.get('snapshot')
        if cached is not None and (now - cached.fetched_at) < self._global_cache_ttl:
            return dict(cached.value)

        flags = await FeatureFlagStore.list_flags()
        # Gather rules per flag in one pass; flags with no rules are global.
        result: dict[str, bool] = {}
        db_keys: set[str] = set()
        for flag in flags:
            db_keys.add(flag.key)
            rules = await FeatureFlagStore.list_rules(flag.key)
            if not rules:
                result[flag.key] = bool(flag.enabled)
        # Env-var-backed flags with no DB row are global by definition.
        for key in _ENV_FLAG_DEFAULTS:
            if key not in db_keys:
                result[key] = _env_fallback(key)
        self._global_cache['snapshot'] = _GlobalCacheEntry(result, now)
        return dict(result)


def _env_fallback(key: str) -> bool:
    """Resolve a flag with no DB row from its registered env-var fallback.

    When the env var is set, its truthiness wins (``true``/``1`` -> allow-all,
    any other value -> deny-all). When the env var is unset, the registered
    ``default_bool`` is used. Unknown/unregistered keys return ``False`` so the
    absence of a DB row never implicitly grants anything.
    """
    entry = _ENV_FLAG_DEFAULTS.get(key)
    if entry is None:
        return False
    val = os.getenv(entry.env_var)
    if val is None:
        return entry.default_bool
    return val.lower() in ('true', '1')


def _rule_matches_context(
    rule: FeatureFlagRule,
    user_id: str | None,
    org_id: str | None,
    email: str | None,
) -> bool:
    """Pure-Python mirror of the store's SQL match conditions.

    Used to re-filter cached rule lists without a DB round-trip.

    A populated rule dimension requires a populated context value to match:
    an anonymous context (no user/org/email) can only match fully-blank
    rules. This prevents a per-user exclude rule from silently excluding
    anonymous callers (and a per-user include from silently granting them),
    so only genuinely global flags reach unauthenticated paths such as the
    web-client config endpoint.
    """
    if rule.user_id is not None and user_id is None:
        return False
    if rule.org_id is not None and org_id is None:
        return False
    if rule.email_pattern is not None and email is None:
        return False
    if rule.user_id is not None and rule.user_id != user_id:
        return False
    if rule.org_id is not None and rule.org_id != org_id:
        return False
    if rule.email_pattern is not None and email is not None:
        if not _sql_like_match(rule.email_pattern, email):
            return False
    return True


def _sql_like_match(pattern: str, value: str) -> bool:
    """Case-insensitive SQL LIKE match: ``%`` = any sequence, ``_`` = one char."""
    import re

    # Escape regex specials, then translate SQL wildcards.
    parts = []
    for ch in pattern.lower():
        if ch == '%':
            parts.append('.*')
        elif ch == '_':
            parts.append('.')
        else:
            parts.append(re.escape(ch))
    regex = re.compile(''.join(parts), re.IGNORECASE)
    return regex.fullmatch(value.lower()) is not None


def _in_percentage_bucket(flag_key: str, user_id: str, percentage: float) -> bool:
    """Deterministic 0-100 bucket.

    Same (flag_key, user_id) always maps to the same bucket, so a user does
    not flicker in/out of a rollout across calls.
    """
    digest = hashlib.sha256(f'{flag_key}:{user_id}'.encode()).digest()
    bucket = int.from_bytes(digest[:4], 'big') % 100
    return bucket < percentage


# Module-level singleton for convenience.
feature_flag_service = FeatureFlagService()
