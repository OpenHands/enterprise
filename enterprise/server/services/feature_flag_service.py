"""Feature flag evaluation service.

Determines whether a flag is enabled for a given context, applying the
precedence: global switch -> exclude rules -> include rules -> percentage
rollout -> default. This mirrors the "whitelist beats blacklist" precedence of
``default_user_authorizer`` generalized to include/exclude + targeting.
"""

import hashlib
import logging
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
        1. Flag missing or globally disabled -> False.
        2. Any matching EXCLUDE rule -> False.
        3. Any matching INCLUDE rule -> True.
        4. A matching INCLUDE rule with a percentage bucket -> deterministic
           hash check; True if in bucket.
        5. Otherwise the flag's global ``enabled`` state (True if on with no
           rules at all; False if rules exist but none matched).
        """
        flag, all_rules = await self._load(key)
        if flag is None or not flag.enabled:
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
        for flag in flags:
            rules = await FeatureFlagStore.list_rules(flag.key)
            if not rules:
                result[flag.key] = bool(flag.enabled)
        self._global_cache['snapshot'] = _GlobalCacheEntry(result, now)
        return dict(result)


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
