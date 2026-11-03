import { useMutation, useQueryClient } from "@tanstack/react-query";
import SettingsService from "#/api/settings-service/settings-service.api";
import { notifySuperAdminSetupStep } from "#/components/features/super-admin/super-admin-setup";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { SETTINGS_QUERY_KEYS } from "#/hooks/query/query-keys";
import type { MCPAuthCredential, MCPConfig } from "#/types/settings";
import { parseMcpConfig, toSdkMcpConfig } from "#/utils/mcp-config";
import { allocateMcpServerName } from "#/utils/mcp-library";

/** A server the MCP Library installs, named after its catalog entry. */
export type McpLibraryServer =
  | {
      type: "sse" | "shttp";
      name: string;
      url: string;
      auth?: MCPAuthCredential;
      headers?: Record<string, string>;
    }
  | {
      type: "stdio";
      name: string;
      command: string;
      args: string[];
      env?: Record<string, string>;
    };

function withServer(config: MCPConfig, server: McpLibraryServer): MCPConfig {
  const next: MCPConfig = {
    sse_servers: [...config.sse_servers],
    stdio_servers: [...config.stdio_servers],
    shttp_servers: [...config.shttp_servers],
  };
  if (server.type === "stdio") {
    next.stdio_servers.push({
      name: server.name,
      command: server.command,
      args: server.args,
      ...(server.env && { env: server.env }),
    });
  } else {
    const remote = {
      name: server.name,
      url: server.url,
      ...(server.auth && { auth: server.auth }),
      ...(server.headers && { headers: server.headers }),
    };
    if (server.type === "sse") next.sse_servers.push(remote);
    else next.shttp_servers.push(remote);
  }
  return next;
}

/**
 * The server as one `mcp_config` entry, the shape `POST /api/v1/mcp/test` and
 * `POST /api/v1/mcp/oauth/start` take.
 */
export function toSdkMcpServer(
  server: McpLibraryServer,
): Record<string, unknown> {
  const empty: MCPConfig = {
    sse_servers: [],
    stdio_servers: [],
    shttp_servers: [],
  };
  return Object.values(toSdkMcpConfig(withServer(empty, server)) ?? {})[0];
}

interface InstallMcpServerVariables {
  server: McpLibraryServer;
  /** Names the server must not take, e.g. the organization's listings. */
  reservedNames?: string[];
}

/**
 * Add a Library server to the member's own MCP servers (OHE-3870). The name
 * gets a `_1`, `_2`, … suffix when the member or the organization already
 * uses it, so a second install never replaces the first and never hides an
 * organization listing. Resolves to the name it was saved under.
 */
export function useInstallMcpServer() {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: async ({
      server,
      reservedNames = [],
    }: InstallMcpServerVariables): Promise<string> => {
      // Fresh settings at save time, like the other MCP server mutations.
      const settings = await SettingsService.getSettings();
      const current = parseMcpConfig(settings?.agent_settings?.mcp_config);
      const takenNames = new Set([
        ...Object.keys(toSdkMcpConfig(current) ?? {}),
        ...reservedNames,
      ]);
      const name = allocateMcpServerName(server.name, takenNames);

      await SettingsService.saveSettings({
        agent_settings_diff: {
          mcp_config: toSdkMcpConfig(withServer(current, { ...server, name })),
        },
      });
      return name;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: SETTINGS_QUERY_KEYS.personal(organizationId),
      });
      notifySuperAdminSetupStep("add-integration");
    },
  });
}
