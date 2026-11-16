/**
 * OpenHands-hosted domains where the free-model UI (badges + info note) is
 * allowed to render. The free-model concept is backed by the `openhands`
 * provider's DB-driven `free` flag, which only exists on OpenHands-managed
 * infrastructure. Self-hosted / arbitrary enterprise installs must never show
 * it, so every free-model render site gates on this helper in addition to the
 * `openhands`-provider + `freeModels` conditions.
 */
const PRODUCTION_HOST = "app.all-hands.dev";
const STAGING_HOST = "staging.all-hands.dev";
const DEVELOPMENT_HOST = "dev.all-hands.dev";
// PR-preview subdomains: pr-<number>.staging.all-hands.dev
const PR_PREVIEW_HOST_PATTERN = /^pr-\d+\.staging\.all-hands\.dev$/;
// Keep this list aligned with the backend's notion of an OpenHands-managed
// host (see SAAS_WEB_HOSTS in migrations/versions/158_*.py and
// _is_all_hands_managed_domain in server/constants.py — the server-side seed
// gates on the same hosts, and the constants file notes the upcoming
// staging.openhands.dev migration).

/**
 * Whether the current page is served from one of the OpenHands-hosted
 * environments (production, staging, development, or a PR-preview subdomain).
 * Returns `false` on self-hosted / arbitrary enterprise domains and during
 * SSR (no `window`), so free-model UI is never rendered there.
 */
export const isOpenHandsHostedDomain = (): boolean => {
  if (typeof window === "undefined") return false;
  const hostname = window.location.hostname.toLowerCase();
  return (
    hostname === PRODUCTION_HOST ||
    hostname === STAGING_HOST ||
    hostname === DEVELOPMENT_HOST ||
    PR_PREVIEW_HOST_PATTERN.test(hostname)
  );
};
