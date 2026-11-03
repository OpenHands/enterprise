import type { TFunction } from "i18next";
import type {
  IntegrationCatalogEntry,
  IntegrationConnectionOption,
  IntegrationOAuthConfig,
  IntegrationTransport,
  MarketplaceField,
} from "@openhands/extensions/integrations";
import { I18nKey } from "#/i18n/declaration";
import type { MCPJsonValue, Provider } from "#/types/settings";

/**
 * Helpers for the MCP Library on Settings > MCP (OHE-3870), ported from the
 * agent-canvas MCP page so both list and install catalog servers the same way.
 */

export type McpLibraryEntry = IntegrationCatalogEntry;
export type McpLibraryField = MarketplaceField;

export type McpLibraryOption = Omit<
  IntegrationConnectionOption,
  "provider" | "transport"
> & {
  provider: "mcp";
  transport: IntegrationTransport;
};

export function getMcpConnectionOptions(
  entry: McpLibraryEntry,
): McpLibraryOption[] {
  return entry.connectionOptions.filter(
    (option): option is McpLibraryOption =>
      option.provider === "mcp" && !!option.transport,
  );
}

/**
 * An OAuth option is installable when the MCP server runs the OAuth flow
 * itself. Catalog provider endpoints describe a hosted integration flow the
 * app server cannot run, so such entries prefer their other options.
 */
function isInstallableMcpOption(option: McpLibraryOption): boolean {
  if (option.auth.strategy !== "oauth2") return true;

  const { oauth } = option.auth;
  if (!oauth) return false;
  return (
    !oauth.authorizationUrl &&
    !oauth.tokenUrl &&
    !oauth.registrationUrl &&
    !oauth.additionalAuthorizationParams &&
    !oauth.additionalTokenParams
  );
}

/**
 * Connects a remote OAuth option through the MCP server's own OAuth
 * discovery and dynamic client registration, dropping the hosted flow's
 * provider endpoints, scopes and client authentication.
 */
function getDiscoveryOAuthOption(
  option: McpLibraryOption,
): McpLibraryOption | undefined {
  if (option.auth.strategy !== "oauth2" || option.transport.kind === "stdio") {
    return undefined;
  }
  return { ...option, auth: { strategy: "oauth2" } };
}

/**
 * The options the install dialog offers, in catalog order; the first is the
 * entry's preferred one. Falls back to OAuth discovery so an entry never
 * dead-ends.
 */
export function getInstallableMcpConnectionOptions(
  entry: McpLibraryEntry,
): McpLibraryOption[] {
  const options = getMcpConnectionOptions(entry);
  const installable = options.filter(isInstallableMcpOption);
  if (installable.length > 0) return installable;
  const fallback = options.map(getDiscoveryOAuthOption).find(Boolean);
  return fallback ? [fallback] : [];
}

export function getDefaultMcpTransport(
  entry: McpLibraryEntry,
): IntegrationTransport | undefined {
  return getMcpConnectionOptions(entry)[0]?.transport;
}

function toOAuthClientAuthMethod(
  value: IntegrationOAuthConfig["clientAuthentication"] | undefined,
): string | undefined {
  switch (value) {
    case "none":
      return "none";
    case "body":
      return "client_secret_post";
    case "basic":
      return "client_secret_basic";
    default:
      return undefined;
  }
}

/** The `auth.authentication` an OAuth option needs, if any. */
export function getMcpOAuthAuthentication(
  option: McpLibraryOption,
): Record<string, MCPJsonValue> | undefined {
  if (option.auth.strategy !== "oauth2") return undefined;
  const authentication: Record<string, MCPJsonValue> = { type: "oauth" };
  const clientAuthMethod = toOAuthClientAuthMethod(
    option.auth.oauth?.clientAuthentication,
  );
  if (clientAuthMethod) {
    authentication.client_auth_method = clientAuthMethod;
  }
  if (option.auth.oauth?.scopes?.length) {
    authentication.scopes = option.auth.oauth.scopes;
  }
  return Object.keys(authentication).length > 1 ? authentication : undefined;
}

/**
 * The catalog entries installable as MCP servers, most popular first (ties
 * keep catalog order).
 */
export function getMcpLibraryEntries(
  catalog: McpLibraryEntry[],
): McpLibraryEntry[] {
  return catalog
    .filter((entry) => getMcpConnectionOptions(entry).length > 0)
    .map((entry, index) => ({ entry, index }))
    .sort(
      (a, b) =>
        (b.entry.popularityRank ?? 0) - (a.entry.popularityRank ?? 0) ||
        a.index - b.index,
    )
    .map(({ entry }) => entry);
}

/** Case-insensitive match on name, description, id and keywords. */
export function mcpLibraryEntryMatchesQuery(
  entry: McpLibraryEntry,
  rawQuery: string,
): boolean {
  const query = rawQuery.trim().toLowerCase();
  if (!query) return true;
  return [entry.name, entry.description, entry.id, ...(entry.keywords ?? [])]
    .join(" ")
    .toLowerCase()
    .includes(query);
}

/** A settings-key-safe server name, e.g. the catalog id "brave-search". */
export function toMcpServerName(value: string, fallback = "mcp"): string {
  const normalized = value
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  return normalized || fallback;
}

/** `base`, or `base_1`, `base_2`, … when the name is taken. */
export function allocateMcpServerName(
  base: string,
  takenNames: ReadonlySet<string>,
): string {
  if (!takenNames.has(base)) return base;
  let suffix = 1;
  while (takenNames.has(`${base}_${suffix}`)) suffix += 1;
  return `${base}_${suffix}`;
}

/** Catalog entries the instance can also connect as a git integration. */
const NATIVE_PROVIDER_BY_ENTRY_ID: Record<string, Provider> = {
  github: "github",
  gitlab: "gitlab",
  bitbucket: "bitbucket",
};

export function getNativeGitProvider(entryId: string): Provider | undefined {
  return NATIVE_PROVIDER_BY_ENTRY_ID[entryId];
}

/** Localized guidance for a failed connection test or OAuth authorization. */
export function makeMcpTestErrorMessage(
  t: TFunction,
  errorKind: string | null | undefined,
  error: string,
  serverType: "sse" | "shttp" | "stdio" | undefined,
): string {
  switch (errorKind) {
    case "timeout":
      return t(I18nKey.MCP$TEST_ERROR_TIMEOUT);
    case "authorization":
      return t(I18nKey.MCP$TEST_ERROR_AUTHORIZATION);
    case "connection":
      return serverType === "stdio"
        ? t(I18nKey.MCP$TEST_ERROR_STDIO_START, { error })
        : t(I18nKey.MCP$TEST_ERROR_CONNECTION, { error });
    case "credentials":
      return t(I18nKey.MCP$TEST_ERROR_CREDENTIALS, { error });
    default:
      return t(I18nKey.MCP$TEST_ERROR_UNKNOWN, { error });
  }
}
