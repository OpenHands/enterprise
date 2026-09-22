import { useContext } from "react";
import { createElement, type ReactNode } from "react";
import {
  IntegrationsHubLiveContext,
  IntegrationsHubLiveProvider,
} from "#/hooks/query/use-integrations-hub-live";
import {
  IntegrationsHubStubContext,
  IntegrationsHubStubProvider,
} from "#/hooks/query/use-integrations-hub-stub";
import type { IntegrationsHubViewModel } from "#/types/integrations-hub";

/**
 * Prefer the live Hub API unless the frontend is running against MSW mocks.
 * Layouts mount this provider when the Hub feature flag is on.
 */
export function shouldUseLiveHubApi() {
  return import.meta.env.VITE_MOCK_API !== "true";
}

export function IntegrationsHubProvider({ children }: { children: ReactNode }) {
  if (shouldUseLiveHubApi()) {
    return createElement(IntegrationsHubLiveProvider, null, children);
  }
  return createElement(IntegrationsHubStubProvider, null, children);
}

export function useIntegrationsHub(): IntegrationsHubViewModel {
  const live = useContext(IntegrationsHubLiveContext);
  const stub = useContext(IntegrationsHubStubContext);
  const value = live ?? stub;
  if (!value) {
    throw new Error(
      "useIntegrationsHub must be used within IntegrationsHubProvider",
    );
  }
  return value;
}
