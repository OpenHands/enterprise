import React from "react";
import { usePostHog } from "posthog-js/react";
import { useConfig } from "./query/use-config";
import { useMe } from "./query/use-me";
import { useGitUser } from "./query/use-git-user";
import { useSettings } from "./query/use-settings";

type PostHogIdentityClient = {
  identify: (distinctId: string, properties?: Record<string, unknown>) => void;
  reset: () => void;
};

function identifyWithAccountChangeReset(
  posthog: PostHogIdentityClient,
  currentDistinctId: string | null,
  distinctId: string,
  properties: Record<string, unknown>,
): string | null {
  if (currentDistinctId === distinctId) return currentDistinctId;
  if (currentDistinctId !== null) posthog.reset();
  posthog.identify(distinctId, properties);
  return distinctId;
}

/**
 * Identifies the current user to PostHog using the same distinct_id
 * that the server-side AnalyticsService uses (keycloak user_id in SaaS
 * mode). This ensures cross-domain tracking works: the anonymous
 * distinct_id bootstrapped from the marketing site gets merged with
 * the keycloak user_id that every server-side event uses.
 *
 * In OSS mode, falls back to the Git user login.
 *
 * Identification is gated on analytics consent, matching the agent canvas
 * UI:
 *  - consent === true  → posthog.identify(...)
 *  - consent === false → posthog.reset() (undo a prior identify)
 *  - consent === null / settings loading → no-op (wait for a decision)
 */
export const usePostHogIdentify = () => {
  const posthog = usePostHog();
  const { data: config } = useConfig();
  const { data: me } = useMe();
  const { data: gitUser } = useGitUser();
  const { data: settings } = useSettings();
  const identifiedIdRef = React.useRef<string | null>(null);

  const consent = settings?.user_consents_to_analytics;

  React.useEffect(() => {
    if (!posthog || settings === undefined) return;

    // Reset on explicit denial to undo any prior identify.
    if (consent === false) {
      if (identifiedIdRef.current !== null) {
        posthog.reset();
        identifiedIdRef.current = null;
      }
      return;
    }

    // Wait for an explicit consent decision before identifying.
    if (consent !== true) return;

    if (config?.app_mode === "saas" && me?.user_id) {
      identifiedIdRef.current = identifyWithAccountChangeReset(
        posthog,
        identifiedIdRef.current,
        me.user_id,
        {
          email: me.email,
        },
      );
    } else if (config?.app_mode === "oss" && gitUser) {
      identifiedIdRef.current = identifyWithAccountChangeReset(
        posthog,
        identifiedIdRef.current,
        gitUser.login,
        {
          company: gitUser.company,
          name: gitUser.name,
          email: gitUser.email,
          user: gitUser.login,
          mode: "oss",
        },
      );
    }
  }, [posthog, config?.app_mode, me, gitUser, consent, settings]);
};
