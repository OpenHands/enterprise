import { useMutation, useQueryClient } from "@tanstack/react-query";
import SettingsService from "#/api/settings-service/settings-service.api";
import { MCPConfig } from "#/types/settings";
import { parseMcpConfig, toSdkMcpConfig } from "#/utils/mcp-config";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { SETTINGS_QUERY_KEYS } from "#/hooks/query/query-keys";

/**
 * Switch one of the member's own MCP servers on or off. A switched-off server
 * keeps its configuration and secrets but is withheld from the agent. The new
 * state is always sent explicitly: the backend leaves a switched-off server
 * off unless a save says `enabled: true`.
 */
export function useToggleMcpServer() {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: async ({
      serverId,
      enabled,
    }: {
      serverId: string;
      enabled: boolean;
    }): Promise<void> => {
      // Fetch fresh settings at mutation time to avoid stale closure issues
      const settings = await SettingsService.getSettings();

      const currentConfig = parseMcpConfig(
        settings?.agent_settings?.mcp_config,
      );

      const newConfig: MCPConfig = {
        sse_servers: [...currentConfig.sse_servers],
        stdio_servers: [...currentConfig.stdio_servers],
        shttp_servers: [...currentConfig.shttp_servers],
      };
      const [serverType, indexStr] = serverId.split("-");
      const index = parseInt(indexStr, 10);

      // A server removed since the list was drawn is left alone.
      if (serverType === "stdio") {
        const current = newConfig.stdio_servers[index];
        if (current) newConfig.stdio_servers[index] = { ...current, enabled };
      } else {
        const servers =
          serverType === "sse"
            ? newConfig.sse_servers
            : newConfig.shttp_servers;
        const current = servers[index];
        if (current) {
          servers[index] = {
            ...(typeof current === "string" ? { url: current } : current),
            enabled,
          };
        }
      }

      const payload = {
        agent_settings_diff: { mcp_config: toSdkMcpConfig(newConfig) },
      };

      await SettingsService.saveSettings(payload);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: SETTINGS_QUERY_KEYS.personal(organizationId),
      });
    },
  });
}
