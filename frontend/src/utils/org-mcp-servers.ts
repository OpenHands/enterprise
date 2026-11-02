import {
  OrgMcpManagedBy,
  OrgMcpServerCreate,
  OrgMcpServerItem,
} from "#/api/org-mcp-servers.types";
import { MCPSHTTPServer, MCPSSEServer } from "#/types/settings";
import { parseMcpConfig } from "#/utils/mcp-config";

/** What the settings API returns in place of a stored credential. */
export const REDACTED_MCP_SECRET = "**********";

export type OrgMcpServerType = "sse" | "shttp";
export type OrgMcpAuthKind = "none" | "bearer" | "header";

/** A listing as the Settings > MCP page shows and edits it. */
export interface OrgMcpServerView {
  name: string;
  managedBy: OrgMcpManagedBy;
  type: OrgMcpServerType;
  url: string;
  timeout?: number;
  auth: OrgMcpAuthKind;
  /** The header carrying the credential (`auth === "header"`). */
  headerName?: string;
  /** What the listing holds where the credential goes, as the API returns
   * it (masked). Sent back unchanged to keep the stored credential. */
  credential?: string;
  /** Personal secrets a user-managed listing needs before it works. */
  secretNames: string[];
}

/** The form's values; `buildOrgMcpServerPayload` turns them into a request. */
export interface OrgMcpServerFormValues {
  name: string;
  managedBy: OrgMcpManagedBy;
  type: OrgMcpServerType;
  url: string;
  timeout?: number;
  auth: OrgMcpAuthKind;
  headerName?: string;
  /** User-managed: the personal secret every member creates to connect. */
  secretName?: string;
  /** Organization-managed: the shared token. Empty while editing keeps the
   * stored one (`keepStoredCredential`). */
  token?: string;
  keepStoredCredential?: boolean;
}

/** Same rule as secret names: a shell-style identifier. */
export const SECRET_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/;
export const ORG_MCP_SERVER_NAME_PATTERN = /^[a-zA-Z0-9_-]+$/;

function firstHeader(
  headers: Record<string, string> | null | undefined,
): [string, string] | undefined {
  const entries = Object.entries(headers ?? {});
  return entries.length > 0 ? entries[0] : undefined;
}

export function toOrgMcpServerView(item: OrgMcpServerItem): OrgMcpServerView {
  const parsed = parseMcpConfig({ [item.name]: item.server });
  const sse = parsed.sse_servers[0];
  const shttp = parsed.shttp_servers[0];
  const entry = sse ?? shttp;
  const server: MCPSSEServer | MCPSHTTPServer | undefined =
    typeof entry === "object" ? entry : undefined;

  let auth: OrgMcpAuthKind = "none";
  let headerName: string | undefined;
  let credential: string | undefined;
  const strategy = server?.auth?.strategy;
  if (
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
  };
}

/** The `${NAME}` reference a user-managed listing stores for its credential. */
export function secretReference(secretName: string): string {
  return `\${${secretName}}`;
}

export function buildOrgMcpServerPayload(
  values: OrgMcpServerFormValues,
): OrgMcpServerCreate {
  const server: Record<string, unknown> = {
    url: values.url,
    transport: values.type === "sse" ? "sse" : "http",
  };
  if (values.type === "shttp" && values.timeout !== undefined) {
    server.timeout = values.timeout;
  }
  if (values.auth !== "none") {
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
  return { name: values.name, managed_by: values.managedBy, server };
}

/** Whether the member holds every personal secret the listing references. */
export function isOrgMcpServerConnected(
  view: OrgMcpServerView,
  personalSecretNames: ReadonlySet<string>,
): boolean {
  return (
    view.managedBy === "org" ||
    view.secretNames.every((name) => personalSecretNames.has(name))
  );
}
