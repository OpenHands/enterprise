export interface HubCatalogLogoBranding {
  iconBg: string;
  iconColor: string;
}

export const DEFAULT_HUB_CATALOG_LOGO_BG = "var(--oh-color-tertiary)";
export const DEFAULT_HUB_CATALOG_LOGO_COLOR = "#FFFFFF";

const WHITE = DEFAULT_HUB_CATALOG_LOGO_COLOR;

/**
 * Brand tile colors for OAuth catalog slugs that do not ship `iconBg`
 * in the extensions catalog JSON. Hex values follow Simple Icons / Hub.
 */
const OAUTH_PROVIDER_LOGO_BRANDING: Record<string, HubCatalogLogoBranding> = {
  "google-docs": { iconBg: "#4285F4", iconColor: WHITE },
  "google-drive": { iconBg: "#4285F4", iconColor: WHITE },
  "google-sheets": { iconBg: "#34A853", iconColor: WHITE },
  gmail: { iconBg: "#EA4335", iconColor: WHITE },
  "google-calendar": { iconBg: "#4285F4", iconColor: WHITE },
  jira: { iconBg: "#0052CC", iconColor: WHITE },
  confluence: { iconBg: "#172B4D", iconColor: WHITE },
  asana: { iconBg: "#F06A6A", iconColor: WHITE },
  trello: { iconBg: "#0052CC", iconColor: WHITE },
  clickup: { iconBg: "#7B68EE", iconColor: WHITE },
  monday: { iconBg: "#6161FF", iconColor: WHITE },
  dropbox: { iconBg: "#0061FF", iconColor: WHITE },
  box: { iconBg: "#0061D5", iconColor: WHITE },
  "microsoft-outlook": { iconBg: "#0078D4", iconColor: WHITE },
  "microsoft-teams": { iconBg: "#6264A7", iconColor: WHITE },
  onedrive: { iconBg: "#0078D4", iconColor: WHITE },
  sharepoint: { iconBg: "#0078D4", iconColor: WHITE },
  salesforce: { iconBg: "#00A1E0", iconColor: WHITE },
  hubspot: { iconBg: "#FF7A59", iconColor: WHITE },
  zendesk: { iconBg: "#03363D", iconColor: WHITE },
  intercom: { iconBg: "#1F1D1D", iconColor: WHITE },
  shopify: { iconBg: "#7AB55C", iconColor: WHITE },
  discord: { iconBg: "#5865F2", iconColor: WHITE },
  zoom: { iconBg: "#0B5CFF", iconColor: WHITE },
  webflow: { iconBg: "#4353FF", iconColor: WHITE },
  miro: { iconBg: "#050038", iconColor: WHITE },
  canva: { iconBg: "#00C4CC", iconColor: WHITE },
  datadog: { iconBg: "#632CA6", iconColor: WHITE },
  posthog: { iconBg: "#1D4AFF", iconColor: WHITE },
  vercel: { iconBg: "#000000", iconColor: WHITE },
  netlify: { iconBg: "#00C7B7", iconColor: WHITE },
  plaid: { iconBg: "#111111", iconColor: WHITE },
  okta: { iconBg: "#007DC1", iconColor: WHITE },
  servicenow: { iconBg: "#62D84E", iconColor: WHITE },
  freshdesk: { iconBg: "#25C16F", iconColor: WHITE },
  pipedrive: { iconBg: "#017737", iconColor: WHITE },
  mailchimp: { iconBg: "#FFE01B", iconColor: "#000000" },
  quickbooks: { iconBg: "#2CA01C", iconColor: WHITE },
  xero: { iconBg: "#13B5EA", iconColor: WHITE },
  gitlab: { iconBg: "#FC6D26", iconColor: WHITE },
  bitbucket: { iconBg: "#0052CC", iconColor: WHITE },
  azure_devops: { iconBg: "#0078D4", iconColor: WHITE },
  forgejo: { iconBg: "#FB923C", iconColor: WHITE },
  ordinal: { iconBg: "#111827", iconColor: WHITE },
  "custom-mcp": {
    iconBg: "var(--oh-interactive-hover)",
    iconColor: WHITE,
  },
};

/** Catalog JSON `iconBg` / `iconColor` from @openhands/extensions. */
const CATALOG_JSON_LOGO_BRANDING: Record<string, HubCatalogLogoBranding> = {
  airtable: { iconBg: "#FCB400", iconColor: "var(--oh-surface-deep)" },
  apify: { iconBg: "#10b981", iconColor: WHITE },
  "atlassian-rovo": { iconBg: "#0052CC", iconColor: WHITE },
  atlassian: { iconBg: "#0052CC", iconColor: WHITE },
  "brave-search": { iconBg: "#FB542B", iconColor: WHITE },
  "browser-mcp": { iconBg: "#0EA5E9", iconColor: WHITE },
  clickhouse: { iconBg: "#FFFF00", iconColor: "var(--oh-surface-deep)" },
  "cloudflare-bindings": { iconBg: "#F38020", iconColor: WHITE },
  "cloudflare-browser-rendering": { iconBg: "#F38020", iconColor: WHITE },
  "cloudflare-builds": { iconBg: "#F38020", iconColor: WHITE },
  "cloudflare-docs": { iconBg: "#F38020", iconColor: WHITE },
  "cloudflare-observability": { iconBg: "#F38020", iconColor: WHITE },
  deepwiki: { iconBg: "var(--oh-color-base)", iconColor: WHITE },
  elevenlabs: { iconBg: "var(--oh-color-base)", iconColor: WHITE },
  everything: { iconBg: "#6366F1", iconColor: WHITE },
  exa: { iconBg: "var(--oh-surface)", iconColor: WHITE },
  fetch: { iconBg: "var(--oh-interactive-hover)", iconColor: WHITE },
  figma: { iconBg: "var(--oh-surface)", iconColor: WHITE },
  filesystem: { iconBg: "var(--oh-interactive-hover)", iconColor: WHITE },
  firecrawl: { iconBg: "#F97316", iconColor: WHITE },
  git: { iconBg: "#F1502F", iconColor: WHITE },
  github: { iconBg: "var(--oh-surface)", iconColor: WHITE },
  huggingface: { iconBg: "#FFD21E", iconColor: "#000000" },
  kagi: { iconBg: "#FFB319", iconColor: "var(--oh-surface-deep)" },
  linear: { iconBg: "#5E6AD2", iconColor: WHITE },
  memory: { iconBg: "#7C3AED", iconColor: WHITE },
  miro: { iconBg: "#FFD02F", iconColor: WHITE },
  monday: { iconBg: "#FF3D00", iconColor: WHITE },
  mongodb: { iconBg: "#00684A", iconColor: WHITE },
  neon: { iconBg: "#00E599", iconColor: "var(--oh-surface-deep)" },
  notion: { iconBg: "#FFFFFF", iconColor: "#000000" },
  obsidian: { iconBg: "#7C3AED", iconColor: WHITE },
  paypal: { iconBg: "#003087", iconColor: WHITE },
  playwright: { iconBg: "#2EAD33", iconColor: WHITE },
  posthog: { iconBg: "#1D4AFF", iconColor: WHITE },
  quickbooks: { iconBg: "#2CA01C", iconColor: WHITE },
  redis: { iconBg: "#DC382D", iconColor: WHITE },
  resend: { iconBg: "var(--oh-surface-deep)", iconColor: WHITE },
  salesforce: { iconBg: "#00A1E0", iconColor: WHITE },
  sentry: { iconBg: "#362D59", iconColor: WHITE },
  "sequential-thinking": { iconBg: "var(--oh-color-base)", iconColor: WHITE },
  slack: { iconBg: "#4A154B", iconColor: WHITE },
  stripe: { iconBg: "#635BFF", iconColor: WHITE },
  supabase: { iconBg: "#3ECF8E", iconColor: WHITE },
  "superhuman-mail": { iconBg: "#FF5C28", iconColor: WHITE },
  tavily: { iconBg: "#2563EB", iconColor: WHITE },
  time: { iconBg: "var(--oh-surface)", iconColor: WHITE },
  vanta: { iconBg: "#003D2B", iconColor: WHITE },
};

/** Overrides catalog defaults where the hub wants a stronger brand tile. */
const INTEGRATION_LOGO_BRANDING_OVERRIDES: Record<
  string,
  HubCatalogLogoBranding
> = {
  github: { iconBg: "#6E40C9", iconColor: WHITE },
  apify: { iconBg: "#111111", iconColor: WHITE },
  "browser-mcp": { iconBg: "#0EA5E9", iconColor: WHITE },
  exa: { iconBg: "#FFFFFF", iconColor: "#0143D9" },
  firecrawl: { iconBg: "#1A1A1A", iconColor: "#FA5D19" },
  huggingface: { iconBg: "#000000", iconColor: "#FFD21E" },
  neon: { iconBg: "#000000", iconColor: "#12FFF7" },
  playwright: { iconBg: "#FFFFFF", iconColor: "#2D4552" },
  "superhuman-mail": { iconBg: "#FF5C28", iconColor: WHITE },
  tavily: { iconBg: "#1F1E1E", iconColor: WHITE },
  vanta: { iconBg: "#240642", iconColor: WHITE },
};

const SLUG_ALIASES: Record<string, string> = {
  bitbucket_data_center: "bitbucket",
  "jira-dc": "jira",
};

const HUB_CATALOG_LOGO_BRANDING: Record<string, HubCatalogLogoBranding> = {
  ...OAUTH_PROVIDER_LOGO_BRANDING,
  ...CATALOG_JSON_LOGO_BRANDING,
  ...INTEGRATION_LOGO_BRANDING_OVERRIDES,
};

export function resolveHubCatalogLogoSlug(slug: string): string {
  return SLUG_ALIASES[slug] ?? slug;
}

export function hasHubCatalogLogoBranding(slug: string): boolean {
  return resolveHubCatalogLogoSlug(slug) in HUB_CATALOG_LOGO_BRANDING;
}

export function getHubCatalogLogoBranding(
  slug: string,
  overrides?: Partial<HubCatalogLogoBranding>,
): HubCatalogLogoBranding {
  const resolved = resolveHubCatalogLogoSlug(slug);
  const branded = HUB_CATALOG_LOGO_BRANDING[resolved] ?? {
    iconBg: DEFAULT_HUB_CATALOG_LOGO_BG,
    iconColor: DEFAULT_HUB_CATALOG_LOGO_COLOR,
  };

  if (resolved in INTEGRATION_LOGO_BRANDING_OVERRIDES) {
    return branded;
  }

  return {
    iconBg: overrides?.iconBg ?? branded.iconBg,
    iconColor: overrides?.iconColor ?? branded.iconColor,
  };
}
