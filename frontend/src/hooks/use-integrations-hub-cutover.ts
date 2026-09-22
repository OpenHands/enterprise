import { useMemo } from "react";
import { useLocalStorage } from "@uidotdev/usehooks";
import { useIntegrationsHub } from "#/hooks/query/use-integrations-hub";
import { useUserProviders } from "#/hooks/use-user-providers";
import {
  LEGACY_INTEGRATIONS_CUTOVER_STORAGE_KEY,
  buildLegacyCutoverItems,
  hubCatalogSlugSet,
  hubConnectedSlugSet,
  type LegacyCutoverItem,
} from "#/components/features/integrations-hub/legacy-integrations-cutover";

export interface UseIntegrationsHubCutoverResult {
  /** True while settings are loading — do not flash the modal yet. */
  isLoading: boolean;
  /** Whether the first-visit cutover modal should be visible. */
  isOpen: boolean;
  /** Legacy connections that still need a Hub reconnect. */
  items: LegacyCutoverItem[];
  dismiss: () => void;
}

/**
 * First-visit recovery modal state for the Settings > Integrations → Hub cutover.
 * Dismissal is stored in localStorage so it only shows once per browser.
 */
export function useIntegrationsHubCutover(): UseIntegrationsHubCutoverResult {
  const { providers, isLoadingSettings } = useUserProviders();
  const hub = useIntegrationsHub();
  const [dismissed, setDismissed] = useLocalStorage<boolean>(
    LEGACY_INTEGRATIONS_CUTOVER_STORAGE_KEY,
    false,
  );

  const items = useMemo(() => {
    const connected = hubConnectedSlugSet(hub.integrations);
    const catalog = hubCatalogSlugSet([
      ...hub.catalogIntegrations,
      ...hub.integrations,
    ]);
    return buildLegacyCutoverItems(providers, connected, catalog);
  }, [hub.catalogIntegrations, hub.integrations, providers]);

  return {
    isLoading: isLoadingSettings,
    isOpen: !isLoadingSettings && !dismissed,
    items,
    dismiss: () => setDismissed(true),
  };
}
