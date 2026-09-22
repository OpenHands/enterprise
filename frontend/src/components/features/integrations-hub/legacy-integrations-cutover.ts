import type { Provider } from "#/types/settings";

/** localStorage key for first-visit cutover modal dismissal. */
export const LEGACY_INTEGRATIONS_CUTOVER_STORAGE_KEY =
  "integrations_hub_legacy_cutover_dismissed";

/**
 * Legacy Settings > Integrations providers that may need a Hub reconnect
 * after `enable_integrations_hub` replaces the old git-settings surface.
 */
export const LEGACY_CUTOVER_PROVIDER_IDS = [
  "github",
  "gitlab",
  "bitbucket",
  "bitbucket_data_center",
  "azure_devops",
  "forgejo",
] as const;

export type LegacyCutoverProviderId =
  (typeof LEGACY_CUTOVER_PROVIDER_IDS)[number];

/** Preferred Hub catalog slug for each legacy provider (null = no Hub entry). */
export const LEGACY_PROVIDER_HUB_SLUG: Record<
  LegacyCutoverProviderId,
  string | null
> = {
  github: "github",
  gitlab: "gitlab",
  bitbucket: "bitbucket",
  bitbucket_data_center: "bitbucket_data_center",
  azure_devops: "azure_devops",
  forgejo: "forgejo",
};

export interface LegacyCutoverItem {
  id: LegacyCutoverProviderId;
  /** Hub catalog slug to open in the connect wizard, when connectable. */
  hubSlug: string | null;
  /** True when the Hub catalog exposes this slug for connect. */
  canReconnectInHub: boolean;
}

export function isLegacyCutoverProviderId(
  value: string,
): value is LegacyCutoverProviderId {
  return (LEGACY_CUTOVER_PROVIDER_IDS as readonly string[]).includes(value);
}

export function legacyCutoverDisplayName(id: LegacyCutoverProviderId): string {
  const labels: Record<LegacyCutoverProviderId, string> = {
    github: "GitHub",
    gitlab: "GitLab",
    bitbucket: "Bitbucket",
    bitbucket_data_center: "Bitbucket Data Center",
    azure_devops: "Azure DevOps",
    forgejo: "Forgejo",
  };
  return labels[id];
}

/**
 * Build the list of legacy connections that still need a Hub reconnect.
 * Providers already connected in Hub are omitted.
 */
export function buildLegacyCutoverItems(
  legacyProviders: readonly Provider[] | readonly string[],
  hubConnectedSlugs: ReadonlySet<string>,
  hubCatalogSlugs: ReadonlySet<string>,
): LegacyCutoverItem[] {
  const seen = new Set<LegacyCutoverProviderId>();

  return legacyProviders.flatMap((provider) => {
    if (!isLegacyCutoverProviderId(provider) || seen.has(provider)) {
      return [];
    }
    seen.add(provider);

    const hubSlug = LEGACY_PROVIDER_HUB_SLUG[provider];
    if (hubSlug && hubConnectedSlugs.has(hubSlug)) {
      return [];
    }

    return [
      {
        id: provider,
        hubSlug,
        canReconnectInHub: hubSlug !== null && hubCatalogSlugs.has(hubSlug),
      },
    ];
  });
}

export function hubConnectedSlugSet(
  integrations: ReadonlyArray<{ slug: string; connected: boolean }>,
): Set<string> {
  return new Set(
    integrations.filter((item) => item.connected).map((item) => item.slug),
  );
}

export function hubCatalogSlugSet(
  catalog: ReadonlyArray<{ slug: string }>,
): Set<string> {
  return new Set(catalog.map((item) => item.slug));
}
