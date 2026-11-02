import { describe, expect, it } from "vitest";
import { OrgMcpServerItem } from "#/api/org-mcp-servers.types";
import {
  REDACTED_MCP_SECRET,
  buildOrgMcpServerPayload,
  isOrgMcpServerConnected,
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

    expect(view).toEqual({
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
});

describe("buildOrgMcpServerPayload", () => {
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
