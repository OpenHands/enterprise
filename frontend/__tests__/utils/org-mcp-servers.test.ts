import { describe, expect, it } from "vitest";
import { OrgMcpServerItem } from "#/api/org-mcp-servers.types";
import {
  REDACTED_MCP_SECRET,
  buildOrgMcpServerPayload,
  isOrgMcpServerConnected,
  orgMcpServerDescription,
  secretReference,
  toOrgMcpServerView,
} from "#/utils/org-mcp-servers";

const BIFROST_URL = "https://bifrost.example/mcp";

function listing(overrides: Partial<OrgMcpServerItem> = {}): OrgMcpServerItem {
  return {
    name: "bifrost",
    managed_by: "user",
    server: {
      url: BIFROST_URL,
      transport: "http",
      auth: { strategy: "bearer", value: REDACTED_MCP_SECRET },
    },
    secret_names: ["BIFROST_TOKEN"],
    ...overrides,
  };
}

describe("toOrgMcpServerView", () => {
  it("reads a bearer listing as the API returns it, credential masked", () => {
    const view = toOrgMcpServerView(listing());

    expect(view).toMatchObject({
      name: "bifrost",
      managedBy: "user",
      type: "shttp",
      url: BIFROST_URL,
      auth: "bearer",
      credential: REDACTED_MCP_SECRET,
      secretNames: ["BIFROST_TOKEN"],
    });
  });

  it("reads an SSE listing with a header credential and a timeout", () => {
    const view = toOrgMcpServerView(
      listing({
        name: "datadog",
        managed_by: "org",
        server: {
          url: "https://dd.example/sse",
          transport: "sse",
          auth: {
            strategy: "header",
            headers: { "X-API-Key": REDACTED_MCP_SECRET },
          },
        },
        secret_names: [],
      }),
    );

    expect(view.type).toBe("sse");
    expect(view.managedBy).toBe("org");
    expect(view.auth).toBe("header");
    expect(view.headerName).toBe("X-API-Key");
    expect(view.credential).toBe(REDACTED_MCP_SECRET);
    expect(view.secretNames).toEqual([]);
  });

  it("reads a listing without credentials as unauthenticated", () => {
    const view = toOrgMcpServerView(
      listing({
        server: { url: BIFROST_URL, transport: "http", timeout: 90 },
        secret_names: [],
      }),
    );

    expect(view.auth).toBe("none");
    expect(view.timeout).toBe(90);
    expect(view.credential).toBeUndefined();
  });

  it("reads a stdio listing with its command line and masked environment", () => {
    const view = toOrgMcpServerView(
      listing({
        name: "slack",
        server: {
          command: "npx",
          args: ["-y", "@slack/mcp"],
          env: { SLACK_BOT_TOKEN: REDACTED_MCP_SECRET },
        },
        secret_names: ["SLACK_TOKEN"],
      }),
    );

    expect(view).toMatchObject({
      type: "stdio",
      command: "npx",
      args: ["-y", "@slack/mcp"],
      env: { SLACK_BOT_TOKEN: REDACTED_MCP_SECRET },
      auth: "none",
      secretNames: ["SLACK_TOKEN"],
    });
    expect(view.url).toBeUndefined();
  });
});

describe("orgMcpServerDescription", () => {
  it("describes a remote listing by its URL and a stdio one by its command line", () => {
    expect(orgMcpServerDescription(toOrgMcpServerView(listing()))).toBe(
      BIFROST_URL,
    );
    expect(
      orgMcpServerDescription(
        toOrgMcpServerView(
          listing({ server: { command: "npx", args: ["-y", "@slack/mcp"] } }),
        ),
      ),
    ).toBe("npx -y @slack/mcp");
  });
});

describe("toOrgMcpServerView with OAuth", () => {
  const OAUTH_SERVER = {
    url: "https://issues.example/mcp",
    transport: "http",
    auth: {
      strategy: "oauth2",
      authentication: {
        type: "oauth",
        client_id: "client-1",
        client_secret: REDACTED_MCP_SECRET,
        scopes: ["read", "write"],
      },
    },
  };

  it("reads a user-managed listing and whether this member authorized it", () => {
    const view = toOrgMcpServerView({
      name: "issue-tracker",
      managed_by: "user",
      server: OAUTH_SERVER,
      secret_names: [],
      connected: false,
    });

    expect(view.auth).toBe("oauth");
    expect(view.oauth).toEqual({
      clientId: "client-1",
      clientSecret: REDACTED_MCP_SECRET,
      scopes: "read write",
      authorized: false,
    });
    expect(view.connected).toBe(false);
    // The masked config goes back as is to authorize.
    expect(view.server).toEqual(OAUTH_SERVER);
  });

  it("reads an organization-managed listing that holds masked tokens", () => {
    const view = toOrgMcpServerView({
      name: "issue-tracker",
      managed_by: "org",
      server: {
        ...OAUTH_SERVER,
        auth: {
          ...OAUTH_SERVER.auth,
          state: { tokens: { access_token: REDACTED_MCP_SECRET } },
        },
      },
      secret_names: [],
    });

    expect(view.oauth?.authorized).toBe(true);
    expect(view.connected).toBeUndefined();
  });
});

describe("buildOrgMcpServerPayload", () => {
  it("sends the OAuth client settings, and the job only when the org holds the tokens", () => {
    const values = {
      name: "issue-tracker",
      type: "shttp" as const,
      url: "https://issues.example/mcp",
      auth: "oauth" as const,
      keepStoredOAuthClientSecret: true,
      oauthScopes: "read, write",
      oauthJobId: "job-1",
    };

    const org = buildOrgMcpServerPayload({ ...values, managedBy: "org" });
    const user = buildOrgMcpServerPayload({ ...values, managedBy: "user" });

    expect(org.oauth_job_id).toBe("job-1");
    expect(org.server.auth).toEqual({
      strategy: "oauth2",
      authentication: {
        type: "oauth",
        client_secret: REDACTED_MCP_SECRET,
        scopes: ["read", "write"],
      },
    });
    expect(user).not.toHaveProperty("oauth_job_id");
  });

  it("stores a user-managed credential as a reference to the personal secret", () => {
    const payload = buildOrgMcpServerPayload({
      name: "bifrost",
      managedBy: "user",
      type: "shttp",
      url: BIFROST_URL,
      timeout: 60,
      auth: "bearer",
      secretName: "BIFROST_TOKEN",
    });

    expect(payload).toEqual({
      name: "bifrost",
      managed_by: "user",
      server: {
        url: BIFROST_URL,
        transport: "http",
        timeout: 60,
        auth: { strategy: "bearer", value: secretReference("BIFROST_TOKEN") },
      },
    });
  });

  it("stores an organization-managed token inline, under a header when asked", () => {
    const payload = buildOrgMcpServerPayload({
      name: "datadog",
      managedBy: "org",
      type: "sse",
      url: "https://dd.example/sse",
      auth: "header",
      headerName: "X-API-Key",
      token: "dd-service-key",
    });

    expect(payload.server).toEqual({
      url: "https://dd.example/sse",
      transport: "sse",
      auth: { strategy: "header", headers: { "X-API-Key": "dd-service-key" } },
    });
  });

  it("sends the redaction marker back to keep a stored token", () => {
    const payload = buildOrgMcpServerPayload({
      name: "datadog",
      managedBy: "org",
      type: "shttp",
      url: "https://dd.example/mcp",
      auth: "bearer",
      keepStoredCredential: true,
    });

    expect(payload.server.auth).toEqual({
      strategy: "bearer",
      value: REDACTED_MCP_SECRET,
    });
  });

  it("omits credentials for an unauthenticated listing", () => {
    const payload = buildOrgMcpServerPayload({
      name: "docs",
      managedBy: "org",
      type: "shttp",
      url: "https://docs.example/mcp",
      auth: "none",
      token: "ignored",
    });

    expect(payload.server).toEqual({
      url: "https://docs.example/mcp",
      transport: "http",
    });
  });

  it("sends a stdio listing as its command, arguments and environment only", () => {
    const payload = buildOrgMcpServerPayload({
      name: "slack",
      managedBy: "user",
      type: "stdio",
      command: "npx",
      args: ["-y", "@slack/mcp"],
      env: { SLACK_BOT_TOKEN: secretReference("SLACK_TOKEN") },
      auth: "none",
    });

    expect(payload).toEqual({
      name: "slack",
      managed_by: "user",
      server: {
        command: "npx",
        args: ["-y", "@slack/mcp"],
        env: { SLACK_BOT_TOKEN: secretReference("SLACK_TOKEN") },
      },
    });
  });

  it("omits empty arguments and environment from a stdio listing", () => {
    const payload = buildOrgMcpServerPayload({
      name: "filesystem",
      managedBy: "org",
      type: "stdio",
      command: "uvx",
      args: [],
      env: {},
      auth: "none",
    });

    expect(payload.server).toEqual({ command: "uvx" });
  });
});

describe("isOrgMcpServerConnected with OAuth", () => {
  const listing = (connected?: boolean) =>
    toOrgMcpServerView({
      name: "issue-tracker",
      managed_by: "user",
      server: {
        url: "https://issues.example/mcp",
        transport: "http",
        auth: { strategy: "oauth2" },
      },
      secret_names: [],
      connected,
    });

  it("follows the member's own authorization, not their secrets", () => {
    expect(isOrgMcpServerConnected(listing(true), new Set())).toBe(true);
    expect(isOrgMcpServerConnected(listing(false), new Set())).toBe(false);
    expect(isOrgMcpServerConnected(listing(), new Set(["ANY"]))).toBe(false);
  });
});

describe("isOrgMcpServerConnected", () => {
  it("treats organization-managed listings as always connected", () => {
    const view = toOrgMcpServerView(listing({ managed_by: "org" }));

    expect(isOrgMcpServerConnected(view, new Set())).toBe(true);
  });

  it("connects a user-managed listing once every referenced secret exists", () => {
    const view = toOrgMcpServerView(
      listing({ secret_names: ["BIFROST_TOKEN", "BIFROST_ORG"] }),
    );

    expect(isOrgMcpServerConnected(view, new Set(["BIFROST_TOKEN"]))).toBe(
      false,
    );
    expect(
      isOrgMcpServerConnected(
        view,
        new Set(["BIFROST_TOKEN", "BIFROST_ORG", "OTHER"]),
      ),
    ).toBe(true);
  });
});
