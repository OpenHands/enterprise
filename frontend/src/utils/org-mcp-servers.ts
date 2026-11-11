import {
  OrgMcpManagedBy,
  OrgMcpServerCreate,
  OrgMcpServerItem,
} from "#/api/org-mcp-servers.types";
import { MCPSHTTPServer, MCPSSEServer } from "#/types/settings";
import { parseMcpConfig } from "#/utils/mcp-config";

/** What the settings API returns in place of a stored credential. */
export const REDACTED_MCP_SECRET = "**********";

export type OrgMcpServerType = "sse" | "shttp" | "stdio";
export type OrgMcpAuthKind = "none" | "bearer" | "header" | "oauth";

/** A listing as the Settings > MCP page shows and edits it. */
export interface OrgMcpServerView {
  name: string;
  managedBy: OrgMcpManagedBy;
  type: OrgMcpServerType;
  /** Remote listings (`type !== "stdio"`). */
  url?: string;
  timeout?: number;
  /** Stdio listings: the command each member's sandbox runs. Env values
   * come back masked, `${NAME}` references included. */
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  auth: OrgMcpAuthKind;
  /** The header carrying the credential (`auth === "header"`). */
  headerName?: string;
  /** What the listing holds where the credential goes, as the API returns
   * it (masked). Sent back unchanged to keep the stored credential. */
  credential?: string;
  /** Personal secrets a user-managed listing needs before it works. */
  secretNames: string[];
  /** `auth === "oauth"`: the client settings and whether the listing holds
   * tokens of its own. The secret comes back masked. */
  oauth?: {
    clientId?: string;
    clientSecret?: string;
    scopes?: string;
    authorized: boolean;
  };
  /** A user-managed OAuth listing: whether this member has authorized it. */
  connected?: boolean;
  /** The masked config, sent back as is to authorize the listing. */
  server: Record<string, unknown>;
}

/** The form's values; `buildOrgMcpServerPayload` turns them into a request. */
export interface OrgMcpServerFormValues {
  name: string;
  managedBy: OrgMcpManagedBy;
  type: OrgMcpServerType;
  url?: string;
  timeout?: number;
  /** Stdio listings. */
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  auth: OrgMcpAuthKind;
  headerName?: string;
  /** User-managed: the personal secret every member creates to connect. */
  secretName?: string;
  /** Organization-managed: the shared token. Empty while editing keeps the
   * stored one (`keepStoredCredential`). */
  token?: string;
  keepStoredCredential?: boolean;
  /** `auth === "oauth"`; all optional: a server may register clients itself. */
  oauthClientId?: string;
  oauthClientSecret?: string;
  /** Empty while editing keeps the stored secret. */
  keepStoredOAuthClientSecret?: boolean;
  /** Space-separated. */
  oauthScopes?: string;
  /** The finished OAuth job holding an organization-managed listing's tokens. */
  oauthJobId?: string;
}

/** What the MCP Library prefills when an admin adds a catalog server to the
 * organization (OHE-3883); the admin supplies the credential in the form. */
export type OrgMcpServerPrefill = Partial<
  Pick<
    OrgMcpServerFormValues,
    | "name"
    | "type"
    | "url"
    | "command"
    | "args"
    | "env"
    | "auth"
    | "headerName"
    | "oauthScopes"
  >
>;

/** Same rule as secret names: a shell-style identifier. */
export const SECRET_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const ORG_MCP_SERVER_NAME_PATTERN = /^[a-zA-Z0-9_-]+$/;

function firstHeader(
  headers: Record<string, string> | null | undefined,
): [string, string] | undefined {
  const entries = Object.entries(headers ?? {});
  return entries.length > 0 ? entries[0] : undefined;
}

function oauthView(auth: {
  authentication?: Record<string, unknown> | null;
  state?: Record<string, unknown> | null;
}): NonNullable<OrgMcpServerView["oauth"]> {
  const { client_id: clientId, client_secret: clientSecret } =
    auth.authentication ?? {};
  const { scopes } = auth.authentication ?? {};
  const tokens = auth.state?.tokens as Record<string, unknown> | undefined;
  return {
    ...(typeof clientId === "string" && { clientId }),
    ...(typeof clientSecret === "string" && { clientSecret }),
    ...(typeof scopes === "string" && { scopes }),
    ...(Array.isArray(scopes) && { scopes: scopes.join(" ") }),
    authorized: !!tokens?.access_token,
  };
}

export function toOrgMcpServerView(item: OrgMcpServerItem): OrgMcpServerView {
  const parsed = parseMcpConfig({ [item.name]: item.server });
  const stdio = parsed.stdio_servers[0];
  if (stdio !== undefined) {
    return {
      name: item.name,
      managedBy: item.managed_by,
      type: "stdio",
      command: stdio.command,
      ...(stdio.args !== undefined && { args: stdio.args }),
      ...(stdio.env !== undefined && { env: stdio.env }),
      auth: "none",
      secretNames: item.secret_names ?? [],
      server: item.server,
    };
  }
  const sse = parsed.sse_servers[0];
  const shttp = parsed.shttp_servers[0];
  const entry = sse ?? shttp;
  const server: MCPSSEServer | MCPSHTTPServer | undefined =
    typeof entry === "object" ? entry : undefined;

  let auth: OrgMcpAuthKind = "none";
  let headerName: string | undefined;
  let credential: string | undefined;
  let oauth: OrgMcpServerView["oauth"];
  const strategy = server?.auth?.strategy;
  if (server?.auth?.strategy === "oauth2") {
    auth = "oauth";
    oauth = oauthView(server.auth);
  } else if (
    server?.auth &&
    (strategy === "bearer" || strategy === "api_key") &&
    "value" in server.auth
  ) {
    auth = "bearer";
    credential = server.auth.value ?? undefined;
  } else if (
    server?.auth &&
    strategy === "header" &&
    "headers" in server.auth
  ) {
    const header = firstHeader(server.auth.headers);
    if (header) {
      [headerName, credential] = header;
      auth = "header";
    }
  } else {
    const header = firstHeader(server?.headers);
    if (header) {
      [headerName, credential] = header;
      auth = "header";
    }
  }

  const url = server?.url ?? (typeof entry === "string" ? entry : "");
  const timeout =
    shttp && typeof shttp === "object" ? shttp.timeout : undefined;

  return {
    name: item.name,
    managedBy: item.managed_by,
    type: sse !== undefined ? "sse" : "shttp",
    url,
    ...(timeout !== undefined && { timeout }),
    auth,
    ...(headerName !== undefined && { headerName }),
    ...(credential !== undefined && { credential }),
    secretNames: item.secret_names ?? [],
    ...(oauth && { oauth }),
    ...(typeof item.connected === "boolean" && { connected: item.connected }),
    server: item.server,
  };
}

/** The `${NAME}` reference a user-managed listing stores for its credential. */
export function secretReference(secretName: string): string {
  return `\${${secretName}}`;
}

/** What a listing points at, for list rows: its URL, or the stdio command line. */
export function orgMcpServerDescription(view: OrgMcpServerView): string {
  if (view.type === "stdio") {
    return [view.command, ...(view.args ?? [])].filter(Boolean).join(" ");
  }
  return view.url ?? "";
}

export function buildOrgMcpServerPayload(
  values: OrgMcpServerFormValues,
): OrgMcpServerCreate {
  if (values.type === "stdio") {
    // Same shape as a personal stdio server (`toSdkMcpConfig`): credentials
    // travel in `env`, which the backend masks on every read.
    const env = values.env ?? {};
    return {
      name: values.name,
      managed_by: values.managedBy,
      server: {
        command: values.command,
        ...(values.args && values.args.length > 0 && { args: values.args }),
        ...(Object.keys(env).length > 0 && { env }),
      },
    };
  }
  const server: Record<string, unknown> = {
    url: values.url,
    transport: values.type === "sse" ? "sse" : "http",
  };
  if (values.type === "shttp" && values.timeout !== undefined) {
    server.timeout = values.timeout;
  }
  if (values.auth === "oauth") {
    const clientSecret = values.keepStoredOAuthClientSecret
      ? REDACTED_MCP_SECRET
      : values.oauthClientSecret;
    const scopes = values.oauthScopes?.split(/[\s,]+/).filter(Boolean) ?? [];
    server.auth = {
      strategy: "oauth2",
      authentication: {
        type: "oauth",
        ...(values.oauthClientId && { client_id: values.oauthClientId }),
        ...(clientSecret && { client_secret: clientSecret }),
        ...(scopes.length > 0 && { scopes }),
      },
    };
  } else if (values.auth !== "none") {
    let credential: string;
    if (values.managedBy === "user") {
      credential = secretReference(values.secretName ?? "");
    } else if (values.keepStoredCredential) {
      credential = REDACTED_MCP_SECRET;
    } else {
      credential = values.token ?? "";
    }
    server.auth =
      values.auth === "bearer"
        ? { strategy: "bearer", value: credential }
        : {
            strategy: "header",
            headers: { [values.headerName ?? ""]: credential },
          };
  }
  return {
    name: values.name,
    managed_by: values.managedBy,
    server,
    ...(values.auth === "oauth" &&
      values.managedBy === "org" &&
      values.oauthJobId && { oauth_job_id: values.oauthJobId }),
  };
}

/**
 * Whether the member can use the listing: it is organization-managed, they
 * authorized it (OAuth), or they hold every personal secret it references.
 */
export function isOrgMcpServerConnected(
  view: OrgMcpServerView,
  personalSecretNames: ReadonlySet<string>,
): boolean {
  if (view.managedBy === "org") return true;
  if (view.auth === "oauth") return view.connected === true;
  return view.secretNames.every((name) => personalSecretNames.has(name));
}
