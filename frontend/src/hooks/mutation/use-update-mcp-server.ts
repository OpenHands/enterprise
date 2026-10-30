import { useMutation, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import i18next from "i18next";
import AgentProfilesService, {
  AgentProfileSaveInput,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import SettingsService from "#/api/settings-service/settings-service.api";
import {
  MCPSHTTPServer,
  MCPConfig,
  MCPSSEServer,
  MCPStdioServer,
} from "#/types/settings";
import { parseMcpConfig, toSdkMcpConfig } from "#/utils/mcp-config";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { SETTINGS_QUERY_KEYS } from "#/hooks/query/query-keys";
import { AGENT_PROFILES_QUERY_KEY } from "#/hooks/query/use-agent-profiles";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";

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

export function withExplicitMcpAuthClear(
  serialized: NonNullable<ReturnType<typeof toSdkMcpConfig>>,
  server: MCPSSEServer | MCPSHTTPServer,
) {
  if (!server.name || !serialized[server.name]) {
    return serialized;
  }
  const headers = Object.fromEntries(
    Object.entries(server.headers ?? {}).filter(
      ([key]) => key.toLowerCase() !== "authorization",
    ),
  );
  return {
    ...serialized,
    [server.name]: {
      ...serialized[server.name],
      auth: null,
      ...(server.headers && { headers }),
    },
  };
}

export function updatedRemoteServer(
  current: string | MCPSSEServer | MCPSHTTPServer,
  server: MCPServerConfig,
): MCPSSEServer | MCPSHTTPServer {
  const updated: MCPSHTTPServer = {
    ...(typeof current === "object" ? current : {}),
    url: server.url!,
  };
  // Renaming changes the config key; the entry keeps its credentials and
  // stored OAuth state.
  if (server.name) updated.name = server.name;
  if (server.type === "shttp" && server.timeout !== undefined) {
    updated.timeout = server.timeout;
  } else {
    delete updated.timeout;
  }
  if (server.api_key) {
    updated.api_key = server.api_key;
  } else {
    delete updated.api_key;
  }
  return updated;
}

/**
 * Point the member's agent profiles that select MCP server ``oldName`` at
 * ``newName``. Only personal profiles can select MCP servers. Best effort: the
 * rename is already saved, so a failure is reported instead of thrown.
 */
export async function repointAgentProfileMcpRefs(
  oldName: string,
  newName: string,
): Promise<void> {
  try {
    const { profiles } = await AgentProfilesService.listProfiles();
    const affected = profiles.filter(
      (p) => p.scope === "personal" && p.mcp_server_refs?.includes(oldName),
    );
    for (const summary of affected) {
      // Sequential: each save is a whole-profile overwrite of a fresh read.
      // eslint-disable-next-line no-await-in-loop
      const { profile } = await AgentProfilesService.getProfile(
        summary.name,
        "personal",
      );
      const { id, name, revision, ...rest } = profile;
      const refs = (profile.mcp_server_refs ?? []).map((ref) =>
        ref === oldName ? newName : ref,
      );
      // eslint-disable-next-line no-await-in-loop
      await AgentProfilesService.saveProfile(
        summary.name,
        { ...rest, mcp_server_refs: refs } as AgentProfileSaveInput,
        "personal",
      );
    }
  } catch (error) {
    // Backends without agent profiles have nothing to update.
    if (isAxiosError(error) && error.response?.status === 404) return;
    displayErrorToast(
      i18next.t(I18nKey.SETTINGS$MCP_RENAME_PROFILES_NOT_UPDATED, {
        name: oldName,
      }),
    );
  }
}

export function useUpdateMcpServer() {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: async ({
      serverId,
      server,
    }: {
      serverId: string;
      server: MCPServerConfig;
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
      let previousRemote: MCPSSEServer | MCPSHTTPServer | undefined;
      let updatedRemote: MCPSSEServer | MCPSHTTPServer | undefined;
      let previousName: string | undefined;

      if (serverType === "sse") {
        const current = newConfig.sse_servers[index];
        previousRemote = typeof current === "object" ? current : undefined;
        previousName = previousRemote?.name;
        updatedRemote = updatedRemoteServer(current, server);
        newConfig.sse_servers[index] = updatedRemote;
      } else if (serverType === "stdio") {
        previousName = newConfig.stdio_servers[index]?.name;
        const stdioServer: MCPStdioServer = {
          name: server.name!,
          command: server.command!,
          ...(server.args && { args: server.args }),
          env: server.env ?? {},
        };
        newConfig.stdio_servers[index] = stdioServer;
      } else if (serverType === "shttp") {
        const current = newConfig.shttp_servers[index];
        previousRemote = typeof current === "object" ? current : undefined;
        previousName = previousRemote?.name;
        updatedRemote = updatedRemoteServer(current, server);
        newConfig.shttp_servers[index] = updatedRemote;
      }

      let serialized = toSdkMcpConfig(newConfig);
      const remoteApiKeyRemoved =
        previousRemote?.api_key !== undefined && !server.api_key;
      if (remoteApiKeyRemoved && serialized && updatedRemote) {
        serialized = withExplicitMcpAuthClear(serialized, updatedRemote);
      }
      const payload = {
        agent_settings_diff: { mcp_config: serialized },
      };

      await SettingsService.saveSettings(payload);

      if (previousName && server.name && server.name !== previousName) {
        await repointAgentProfileMcpRefs(previousName, server.name);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: SETTINGS_QUERY_KEYS.personal(organizationId),
      });
      queryClient.invalidateQueries({ queryKey: [AGENT_PROFILES_QUERY_KEY] });
    },
  });
}
