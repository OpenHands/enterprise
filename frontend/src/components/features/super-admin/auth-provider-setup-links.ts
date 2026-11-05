import type { OAuthProviderBrand } from "#/api/auth-providers-service/auth-providers.types";
import { I18nKey } from "#/i18n/declaration";

export type ProviderSetupLinkKind = "create" | "list" | "admin" | "docs";

export interface ProviderSetupLink {
  kind: ProviderSetupLinkKind;
  url: string;
  /** Name of the external service the link opens, shown in its label. */
  service: string;
}

export const PROVIDER_SETUP_LINK_LABELS: Record<
  ProviderSetupLinkKind,
  I18nKey
> = {
  create: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SETUP_LINK_CREATE,
  list: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SETUP_LINK_LIST,
  admin: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SETUP_LINK_ADMIN,
  docs: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SETUP_LINK_DOCS,
};

const DEFAULT_ORIGINS: Partial<Record<OAuthProviderBrand, string>> = {
  github: "https://github.com",
  gitlab: "https://gitlab.com",
};

// Presets seed hosts like YOUR_BITBUCKET_HOST / PLACEHOLDER.COM until the
// admin fills them in; a link to those would only 404.
const PLACEHOLDER_HOST = /^(your[_-]|placeholder)/i;

/** The provider's web origin, taken from the first usable URL the admin has
 * entered (so GitHub Enterprise Server / self-managed GitLab links follow
 * their own host), else the brand's public default. */
function providerOrigin(
  brand: OAuthProviderBrand,
  urls: (string | null)[],
): string | null {
  for (const url of urls) {
    if (url) {
      try {
        const { origin, hostname } = new URL(url);
        if (!PLACEHOLDER_HOST.test(hostname)) {
          return origin;
        }
      } catch {
        // Not a URL yet -- try the next one.
      }
    }
  }
  return DEFAULT_ORIGINS[brand] ?? null;
}

const BITBUCKET_CLOUD_DOCS =
  "https://support.atlassian.com/bitbucket-cloud/docs/use-oauth-on-bitbucket-cloud/";
const BITBUCKET_DC_DOCS =
  "https://confluence.atlassian.com/bitbucketserver/configure-an-incoming-link-1108483657.html";
const ENTRA_APP_REGISTRATIONS =
  "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade";
const ENTRA_REGISTER_APP_DOCS =
  "https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app";

/** Links into the external provider's UI where the OAuth app this row needs
 * (client ID + secret + callback URL) is created or listed. Empty when there
 * is nothing useful to link to. `urls` are the row's endpoint URLs that share
 * the provider's web host (authorization, token) -- not userinfo, which is on
 * `api.github.com` for github.com. */
export function getProviderSetupLinks(
  brand: OAuthProviderBrand,
  urls: (string | null)[],
): ProviderSetupLink[] {
  const origin = providerOrigin(brand, urls);

  switch (brand) {
    case "github":
      return origin
        ? [
            {
              kind: "create",
              url: `${origin}/settings/applications/new`,
              service: "GitHub",
            },
            {
              kind: "list",
              url: `${origin}/settings/developers`,
              service: "GitHub",
            },
          ]
        : [];
    case "gitlab":
      return origin
        ? [
            {
              kind: "list",
              url: `${origin}/-/user_settings/applications`,
              service: "GitLab",
            },
            {
              kind: "admin",
              url: `${origin}/admin/applications`,
              service: "GitLab",
            },
          ]
        : [];
    case "forgejo":
      return origin
        ? [
            {
              kind: "list",
              url: `${origin}/user/settings/applications`,
              service: "Forgejo",
            },
          ]
        : [];
    case "bitbucket":
      return [
        { kind: "docs", url: BITBUCKET_CLOUD_DOCS, service: "Atlassian" },
      ];
    case "bitbucket_data_center":
      return [{ kind: "docs", url: BITBUCKET_DC_DOCS, service: "Atlassian" }];
    case "azure_devops":
      return [
        {
          kind: "list",
          url: ENTRA_APP_REGISTRATIONS,
          service: "Microsoft Entra",
        },
        {
          kind: "docs",
          url: ENTRA_REGISTER_APP_DOCS,
          service: "Microsoft Learn",
        },
      ];
    default:
      return [];
  }
}
