import { useMutation } from "@tanstack/react-query";
import McpService from "#/api/mcp-service/mcp-service.api";
import { MCPTestResponse } from "#/api/mcp-service/mcp-service.types";
import SettingsService from "#/api/settings-service/settings-service.api";
import {
  updatedRemoteServer,
  withExplicitMcpAuthClear,
} from "#/hooks/mutation/use-update-mcp-server";
import { MCPConfig, MCPSHTTPServer, MCPSSEServer } from "#/types/settings";
import { parseMcpConfig, toSdkMcpConfig } from "#/utils/mcp-config";

type MCPServerType = "sse" | "stdio" | "shttp";

interface MCPServerConfig {
  type: MCPServerType;
  name?: string;
  url?: string;
  api_key?: string;
  timeout?: number;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
}

interface TestMcpServerVariables {
  /** `${type}-${index}` id of the server being edited; omitted when adding. */
  serverId?: string;
  server: MCPServerConfig;
}

/**
 * Probe a remote MCP server through `POST /api/v1/mcp/test`.
 *
 * The request is serialized exactly like a save would be (same merge with the
 * stored entry, same `toSdkMcpConfig` output), so the test exercises the
 * credentials that will actually be persisted — including unchanged ones the
 * settings round-trip returns redacted, which the backend restores.
 */
export function useTestMcpServer() {
  return useMutation({
    mutationFn: async ({
      serverId,
      server,
    }: TestMcpServerVariables): Promise<MCPTestResponse> => {
      if (server.type === "stdio") {
        throw new Error("stdio MCP servers cannot be tested from settings.");
      }
      // The form validates before calling this, but make the contract explicit
      // for any future caller that skips validation.
      if (!server.url) {
        throw new Error(
          "An MCP server URL is required to test the connection.",
        );
      }

      let entry: MCPSSEServer | MCPSHTTPServer = {
        url: server.url,
        ...(server.api_key && { api_key: server.api_key }),
        ...(server.type === "shttp" &&
          server.timeout !== undefined && { timeout: server.timeout }),
      };
      let previous: MCPSSEServer | MCPSHTTPServer | undefined;

      if (serverId) {
        const settings = await SettingsService.getSettings();
        const currentConfig = parseMcpConfig(
          settings?.agent_settings?.mcp_config,
        );
        const [serverType, indexStr] = serverId.split("-");
        const index = parseInt(indexStr, 10);
        const current =
          serverType === "sse"
            ? currentConfig.sse_servers[index]
            : currentConfig.shttp_servers[index];
        if (current !== undefined) {
          previous = typeof current === "object" ? current : undefined;
          entry = updatedRemoteServer(current, server);
        }
      }

      const config: MCPConfig = {
        sse_servers: server.type === "sse" ? [entry] : [],
        stdio_servers: [],
        shttp_servers: server.type === "shttp" ? [entry] : [],
      };
      let serialized = toSdkMcpConfig(config);
      const remoteApiKeyRemoved =
        previous?.api_key !== undefined && !server.api_key;
      if (remoteApiKeyRemoved && serialized) {
        serialized = withExplicitMcpAuthClear(serialized, entry);
      }

      const [name, sdkServer] = Object.entries(serialized ?? {})[0] ?? [];
      if (!name || !sdkServer) {
        throw new Error("MCP server configuration is incomplete.");
      }

      return McpService.testServer({ name, server: sdkServer });
    },
  });
}
