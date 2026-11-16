import { describe, expect, it } from "vitest";
import {
  toOrgMcpServerPrefill,
  type McpLibraryEntry,
  type McpLibraryOption,
} from "#/utils/mcp-library";
import { secretReference } from "#/utils/org-mcp-servers";

const SERVER_URL = "https://mcp.example.com/mcp";

const ENTRY: McpLibraryEntry = {
  id: "example",
  name: "Example",
  description: "An example MCP server",
  docsUrl: "https://example.com/docs",
  connectionOptions: [],
};

function option(overrides: Partial<McpLibraryOption> = {}): McpLibraryOption {
  return {
    id: "api",
    provider: "mcp",
    transport: { kind: "shttp", url: SERVER_URL },
    auth: { strategy: "api_key" },
    ...overrides,
  };
}

describe("toOrgMcpServerPrefill", () => {
  it("prefills a bearer listing for an API key sent as a Bearer token", () => {
    expect(toOrgMcpServerPrefill(ENTRY, option())).toEqual({
      name: "example",
      type: "shttp",
      url: SERVER_URL,
      auth: "bearer",
    });
  });

  it("prefills a header listing from the credential's header name", () => {
    const prefill = toOrgMcpServerPrefill(
      ENTRY,
      option({ auth: { strategy: "api_key", apiKeyHeaderName: "X-API-Key" } }),
    );

    expect(prefill).toMatchObject({ auth: "header", headerName: "X-API-Key" });
  });

  it("prefills a header listing from the first header field", () => {
    const prefill = toOrgMcpServerPrefill(
      ENTRY,
      option({
        transport: {
          kind: "sse",
          url: SERVER_URL,
          headerFields: [
            {
              key: "DD-API-KEY",
              label: "API key",
              type: "password",
              required: true,
            },
            {
              key: "DD-APPLICATION-KEY",
              label: "Application key",
              type: "password",
              required: true,
            },
          ],
        },
        auth: { strategy: "none" },
      }),
    );

    expect(prefill).toMatchObject({
      type: "sse",
      auth: "header",
      headerName: "DD-API-KEY",
    });
  });

  it("prefills an OAuth listing with the catalog's scopes", () => {
    const prefill = toOrgMcpServerPrefill(
      ENTRY,
      option({
        auth: { strategy: "oauth2", oauth: { scopes: ["read", "write"] } },
      }),
    );

    expect(prefill).toMatchObject({ auth: "oauth", oauthScopes: "read write" });
    expect(prefill).not.toHaveProperty("headerName");
  });

  it("prefills a stdio listing with its command and a secret reference per required variable", () => {
    const prefill = toOrgMcpServerPrefill(
      ENTRY,
      option({
        transport: {
          kind: "stdio",
          serverName: "example_server",
          command: "npx",
          args: ["-y", "example-mcp"],
          envFields: [
            {
              key: "EXAMPLE_TOKEN",
              label: "Token",
              type: "password",
              required: true,
            },
            {
              key: "EXAMPLE_HOST",
              label: "Host",
              type: "text",
              required: false,
            },
          ],
        },
      }),
    );

    expect(prefill).toEqual({
      name: "example_server",
      type: "stdio",
      command: "npx",
      args: ["-y", "example-mcp"],
      env: { EXAMPLE_TOKEN: secretReference("EXAMPLE_TOKEN") },
    });
  });
});
