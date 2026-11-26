import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";
import AgentProfilesService from "#/api/agent-profiles-service/agent-profiles-service.api";
import McpService from "#/api/mcp-service/mcp-service.api";
import SettingsService from "#/api/settings-service/settings-service.api";
import { useAddMcpServer } from "#/hooks/mutation/use-add-mcp-server";
import { useTestMcpServer } from "#/hooks/mutation/use-test-mcp-server";
import { useDeleteMcpServer } from "#/hooks/mutation/use-delete-mcp-server";
import {
  repointAgentProfileMcpRefs,
  useUpdateMcpServer,
} from "#/hooks/mutation/use-update-mcp-server";
import * as toasts from "#/utils/custom-toast-handlers";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import type { Settings } from "#/types/settings";

describe("MCP Server Mutation Hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useSelectedOrganizationStore.setState({ organizationId: "test-org-id" });
  });

  const createWrapper = () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });
    return ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };

  // SDK 1.31.x dropped the ``mcpServers`` wrapper from ``model_dump``, so the
  // settings endpoint now returns a flat server map. ``parseMcpConfig`` still
  // accepts the legacy wrapped shape, and ``toSdkMcpConfig`` now emits the
  // flat shape. Tests below cover both directions.

  describe("useAddMcpServer", () => {
    it("fetches fresh settings at mutation time", async () => {
      const getSettingsSpy = vi
        .spyOn(SettingsService, "getSettings")
        .mockResolvedValue({
          agent_settings: {
            mcp_config: {
              existing: { url: "https://existing.com", transport: "sse" },
            },
          },
        } as any);

      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);

      const { result } = renderHook(() => useAddMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        type: "sse",
        url: "https://new-server.com",
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(getSettingsSpy).toHaveBeenCalledTimes(1);
      expect(saveSettingsSpy).toHaveBeenCalledWith({
        agent_settings_diff: {
          mcp_config: expect.objectContaining({
            existing: expect.objectContaining({ url: "https://existing.com" }),
          }),
        },
      });
      // Should not wrap in the legacy ``mcpServers`` envelope.
      const savedConfig = (
        saveSettingsSpy.mock.calls[0][0] as {
          agent_settings_diff: { mcp_config: Record<string, unknown> };
        }
      ).agent_settings_diff.mcp_config;
      expect(savedConfig).not.toHaveProperty("mcpServers");
    });

    it("also accepts the legacy wrapped shape on read", async () => {
      // Backwards-compat: a server persisted before the SDK 1.31.x bump may
      // still come back as ``{ mcpServers: {...} }`` if the value has not yet
      // been rewritten by the backend. The frontend must still parse it.
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            mcpServers: {
              existing: { url: "https://existing.com", transport: "sse" },
            },
          },
        },
      } as any);

      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);

      const { result } = renderHook(() => useAddMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({ type: "sse", url: "https://new-server.com" });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(saveSettingsSpy).toHaveBeenCalledWith({
        agent_settings_diff: {
          mcp_config: expect.objectContaining({
            existing: expect.objectContaining({ url: "https://existing.com" }),
          }),
        },
      });
    });

    it("handles adding server when no existing config", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {},
      } as any);

      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);

      const { result } = renderHook(() => useAddMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        type: "sse",
        url: "https://first-server.com",
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(saveSettingsSpy).toHaveBeenCalledWith({
        agent_settings_diff: {
          mcp_config: {
            sse: {
              url: "https://first-server.com",
              transport: "sse",
            },
          },
        },
      });
    });

    it("proceeds with empty config when getSettings returns null", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue(null as any);

      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);

      const { result } = renderHook(() => useAddMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        type: "sse",
        url: "https://server.com",
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(saveSettingsSpy).toHaveBeenCalledWith({
        agent_settings_diff: {
          mcp_config: {
            sse: {
              url: "https://server.com",
              transport: "sse",
            },
          },
        },
      });
    });

    it("leaves a switched-off server off when a different server is added", async () => {
      // Arrange
      const switchedOff = {
        url: "https://docs.example.com/mcp",
        description: "Product docs",
        enabled: false,
      };
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: { mcp_config: { docs: switchedOff } },
      } as unknown as Settings);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useAddMcpServer(), {
        wrapper: createWrapper(),
      });

      // Act
      result.current.mutate({
        type: "shttp",
        name: "search",
        url: "https://search.example.com/mcp",
      });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      // Assert
      expect(saveSettingsSpy).toHaveBeenCalledWith({
        agent_settings_diff: {
          mcp_config: {
            docs: switchedOff,
            search: { url: "https://search.example.com/mcp" },
          },
        },
      });
    });
  });

  describe("useAddMcpServer remote server names", () => {
    it("saves a remote server under the name it was given", async () => {
      // Arrange
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            shttp: { url: "https://other.example.com/mcp" },
          },
        },
      } as any);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useAddMcpServer(), {
        wrapper: createWrapper(),
      });

      // Act
      result.current.mutate({
        type: "shttp",
        name: "sonarqube",
        url: "https://tools.example.com/sonarqube-mcp/mcp",
        api_key: "${SONARQUBE_TOKEN}",
      });
      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      // Assert
      const savedConfig = (
        saveSettingsSpy.mock.calls[0][0] as {
          agent_settings_diff: { mcp_config: Record<string, unknown> };
        }
      ).agent_settings_diff.mcp_config;
      expect(Object.keys(savedConfig).sort()).toEqual(["shttp", "sonarqube"]);
      expect(savedConfig.sonarqube).toEqual(
        expect.objectContaining({
          url: "https://tools.example.com/sonarqube-mcp/mcp",
          auth: { strategy: "bearer", value: "${SONARQUBE_TOKEN}" },
        }),
      );
    });
  });

  describe("useDeleteMcpServer", () => {
    it("deletes the correct server by index", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            server1: { url: "https://server1.com", transport: "sse" },
            server2: { url: "https://server2.com", transport: "sse" },
            server3: { url: "https://server3.com", transport: "sse" },
          },
        },
      } as any);

      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);

      const { result } = renderHook(() => useDeleteMcpServer(), {
        wrapper: createWrapper(),
      });

      // Use hyphen separator as per implementation: serverId.split("-")
      result.current.mutate("sse-1");

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      const savedPayload = saveSettingsSpy.mock.calls[0][0] as {
        agent_settings_diff: {
          mcp_config: Record<string, unknown> | null;
        };
      };
      const savedConfig = savedPayload.agent_settings_diff.mcp_config;
      const serverNames = Object.keys(savedConfig ?? {});
      expect(serverNames).toHaveLength(2);
    });

    it("handles deleting from empty config", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: null,
        },
      } as any);

      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);

      const { result } = renderHook(() => useDeleteMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate("sse-0");

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(saveSettingsSpy).toHaveBeenCalled();
    });
  });

  describe("useUpdateMcpServer", () => {
    it("updates the correct server URL", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            myserver: { url: "https://old-url.com", transport: "sse" },
          },
        },
      } as any);

      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);

      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      // Use hyphen separator as per implementation
      result.current.mutate({
        serverId: "sse-0",
        server: {
          type: "sse",
          url: "https://new-url.com",
        },
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      const savedPayload = saveSettingsSpy.mock.calls[0][0] as {
        agent_settings_diff: {
          mcp_config: Record<string, { url: string }>;
        };
      };
      const savedConfig = savedPayload.agent_settings_diff.mcp_config;
      const serverUrls = Object.values(savedConfig).map((s) => s.url);
      expect(serverUrls).toContain("https://new-url.com");
      expect(savedConfig).toHaveProperty("myserver");
    });

    it("removes a cleared SHTTP timeout", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            myserver: {
              url: "https://server.example.com",
              timeout: 45,
            },
          },
        },
      } as unknown as Settings);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        serverId: "shttp-0",
        server: { type: "shttp", url: "https://server.example.com" },
      });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      const payload = saveSettingsSpy.mock.calls[0][0] as {
        agent_settings_diff: {
          mcp_config: Record<string, { timeout?: number }>;
        };
      };
      expect(
        payload.agent_settings_diff.mcp_config.myserver,
      ).not.toHaveProperty("timeout");
    });

    it("keeps a stdio server's stored settings the form does not edit", async () => {
      // Arrange
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            files: {
              command: "npx",
              args: ["-y", "fs-server"],
              cwd: "/workspace",
              description: "Workspace files",
              icon: "folder",
              timeout: 20,
              enabled: false,
            },
          },
        },
      } as unknown as Settings);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      // Act
      result.current.mutate({
        serverId: "stdio-0",
        server: {
          type: "stdio",
          name: "files",
          command: "npx",
          args: ["-y", "fs-server", "--read-only"],
        },
      });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      // Assert
      expect(saveSettingsSpy).toHaveBeenCalledWith({
        agent_settings_diff: {
          mcp_config: {
            files: {
              command: "npx",
              args: ["-y", "fs-server", "--read-only"],
              env: {},
              cwd: "/workspace",
              description: "Workspace files",
              icon: "folder",
              timeout: 20,
              enabled: false,
            },
          },
        },
      });
    });

    it("keeps an SSE server's stored settings the form does not edit", async () => {
      // Arrange
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            events: {
              url: "https://events.example.com/sse",
              transport: "sse",
              description: "Event stream",
              icon: "https://events.example.com/icon.png",
              timeout: 45,
              enabled: false,
            },
          },
        },
      } as unknown as Settings);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      // Act
      result.current.mutate({
        serverId: "sse-0",
        server: {
          type: "sse",
          name: "events",
          url: "https://events.example.com/v2/sse",
        },
      });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));

      // Assert
      expect(saveSettingsSpy).toHaveBeenCalledWith({
        agent_settings_diff: {
          mcp_config: {
            events: {
              url: "https://events.example.com/v2/sse",
              transport: "sse",
              description: "Event stream",
              icon: "https://events.example.com/icon.png",
              timeout: 45,
              enabled: false,
            },
          },
        },
      });
    });

    it("sends explicit null when removing remote auth", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            myserver: {
              url: "https://server.example.com",
              auth: { strategy: "bearer", value: "**********" },
            },
          },
        },
      } as unknown as Settings);

      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        serverId: "shttp-0",
        server: {
          type: "shttp",
          url: "https://server.example.com",
        },
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      const savedPayload = saveSettingsSpy.mock.calls[0][0] as {
        agent_settings_diff: {
          mcp_config: Record<string, { auth?: unknown }>;
        };
      };
      const savedConfig = savedPayload.agent_settings_diff.mcp_config;
      expect(savedConfig.myserver).toHaveProperty("auth", null);
    });

    it.each([
      {
        strategy: "basic",
        username: "user",
        password: "**********",
      },
      {
        strategy: "header",
        headers: { "X-API-Key": "**********" },
      },
      {
        strategy: "oauth2",
        state: { tokens: { access_token: "**********" } },
      },
    ])("preserves $strategy auth on unrelated edits", async (auth) => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            myserver: {
              url: "https://server.example.com",
              auth,
            },
          },
        },
      } as unknown as Settings);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        serverId: "shttp-0",
        server: {
          type: "shttp",
          url: "https://server.example.com",
          timeout: 45,
        },
      });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      const payload = saveSettingsSpy.mock.calls[0][0] as {
        agent_settings_diff: {
          mcp_config: Record<string, { auth?: unknown }>;
        };
      };
      expect(payload.agent_settings_diff.mcp_config.myserver.auth).toEqual(
        auth,
      );
    });

    it("preserves custom API-key strategy and headers on edit", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            myserver: {
              url: "https://server.example.com",
              headers: { "X-Tenant": "**********" },
              auth: {
                strategy: "api_key",
                value: "**********",
                header_name: "X-API-Key",
              },
            },
          },
        },
      } as unknown as Settings);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        serverId: "shttp-0",
        server: {
          type: "shttp",
          url: "https://server.example.com",
          api_key: "**********",
        },
      });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      const payload = saveSettingsSpy.mock.calls[0][0] as {
        agent_settings_diff: {
          mcp_config: Record<string, { auth?: unknown; headers?: unknown }>;
        };
      };
      expect(payload.agent_settings_diff.mcp_config.myserver).toMatchObject({
        headers: { "X-Tenant": "**********" },
        auth: {
          strategy: "api_key",
          value: "**********",
          header_name: "X-API-Key",
        },
      });
    });

    it("removes an Authorization header without clearing custom headers", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            myserver: {
              url: "https://server.example.com",
              headers: {
                Authorization: "Bearer **********",
                "X-Tenant": "**********",
              },
            },
          },
        },
      } as unknown as Settings);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        serverId: "shttp-0",
        server: { type: "shttp", url: "https://server.example.com" },
      });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      const payload = saveSettingsSpy.mock.calls[0][0] as {
        agent_settings_diff: {
          mcp_config: Record<string, { auth?: unknown; headers?: unknown }>;
        };
      };
      expect(payload.agent_settings_diff.mcp_config.myserver).toMatchObject({
        auth: null,
        headers: { "X-Tenant": "**********" },
      });
    });
  });

  describe("useUpdateMcpServer renames", () => {
    it("renames a remote server and keeps its redacted credential for the backend to restore", async () => {
      // Arrange
      const listSpy = vi
        .spyOn(AgentProfilesService, "listProfiles")
        .mockResolvedValue({ profiles: [], active_agent_profile_id: null });
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            shttp: {
              url: "https://tools.example.com/sonarqube-mcp/mcp",
              auth: { strategy: "bearer", value: "**********" },
              timeout: 30,
            },
          },
        },
      } as any);
      const saveSettingsSpy = vi
        .spyOn(SettingsService, "saveSettings")
        .mockResolvedValue(true);
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      // Act
      result.current.mutate({
        serverId: "shttp-0",
        server: {
          type: "shttp",
          name: "sonarqube",
          url: "https://tools.example.com/sonarqube-mcp/mcp",
          api_key: "**********",
          timeout: 30,
        },
      });
      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      // Assert: the old key is gone and the backend matches the redacted
      // credential to the stored server by URL.
      const savedConfig = (
        saveSettingsSpy.mock.calls[0][0] as {
          agent_settings_diff: { mcp_config: Record<string, unknown> };
        }
      ).agent_settings_diff.mcp_config;
      expect(Object.keys(savedConfig)).toEqual(["sonarqube"]);
      expect(savedConfig.sonarqube).toEqual({
        url: "https://tools.example.com/sonarqube-mcp/mcp",
        auth: { strategy: "bearer", value: "**********" },
        timeout: 30,
      });
      expect(listSpy).toHaveBeenCalledTimes(1);
    });

    it("leaves agent profiles alone when the server keeps its name", async () => {
      // Arrange
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: { shttp: { url: "https://old.example.com/mcp" } },
        },
      } as any);
      vi.spyOn(SettingsService, "saveSettings").mockResolvedValue(true);
      const listSpy = vi.spyOn(AgentProfilesService, "listProfiles");
      const { result } = renderHook(() => useUpdateMcpServer(), {
        wrapper: createWrapper(),
      });

      // Act
      result.current.mutate({
        serverId: "shttp-0",
        server: {
          type: "shttp",
          name: "shttp",
          url: "https://new.example.com/mcp",
        },
      });
      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      // Assert
      expect(listSpy).not.toHaveBeenCalled();
    });
  });

  describe("repointAgentProfileMcpRefs", () => {
    const summary = (
      name: string,
      scope: "personal" | "org",
      refs: string[] | null,
    ) => ({
      id: `${name}-id`,
      name,
      agent_kind: "openhands" as const,
      revision: 3,
      llm_profile_ref: "default",
      mcp_server_refs: refs,
      scope,
    });

    it("points the personal profiles that use the old name at the new one", async () => {
      // Arrange
      vi.spyOn(AgentProfilesService, "listProfiles").mockResolvedValue({
        profiles: [
          summary("reviewer", "personal", ["shttp", "github"]),
          summary("writer", "personal", ["github"]),
          summary("everything", "personal", null),
          summary("team", "org", ["shttp"]),
        ],
        active_agent_profile_id: null,
      });
      vi.spyOn(AgentProfilesService, "getProfile").mockResolvedValue({
        name: "reviewer",
        scope: "personal",
        profile: {
          id: "reviewer-id",
          name: "reviewer",
          revision: 3,
          agent_kind: "openhands",
          llm_profile_ref: "default",
          mcp_server_refs: ["shttp", "github"],
          persona: "Reviews code",
        },
      });
      const saveSpy = vi
        .spyOn(AgentProfilesService, "saveProfile")
        .mockResolvedValue();

      // Act
      await repointAgentProfileMcpRefs("shttp", "sonarqube");

      // Assert
      expect(saveSpy).toHaveBeenCalledTimes(1);
      expect(saveSpy).toHaveBeenCalledWith(
        "reviewer",
        {
          agent_kind: "openhands",
          llm_profile_ref: "default",
          mcp_server_refs: ["sonarqube", "github"],
          persona: "Reviews code",
        },
        "personal",
      );
    });

    it("does nothing on a backend without agent profiles", async () => {
      // Arrange
      vi.spyOn(AgentProfilesService, "listProfiles").mockRejectedValue(
        new AxiosError("Not Found", "ERR_BAD_REQUEST", undefined, undefined, {
          status: 404,
          statusText: "Not Found",
          data: {},
          headers: {},
          config: { headers: new AxiosHeaders() },
        }),
      );
      const toastSpy = vi
        .spyOn(toasts, "displayErrorToast")
        .mockImplementation(() => {});

      // Act
      await repointAgentProfileMcpRefs("shttp", "sonarqube");

      // Assert
      expect(toastSpy).not.toHaveBeenCalled();
    });

    it("reports profiles it could not update without failing the rename", async () => {
      // Arrange
      vi.spyOn(AgentProfilesService, "listProfiles").mockResolvedValue({
        profiles: [summary("reviewer", "personal", ["shttp"])],
        active_agent_profile_id: null,
      });
      vi.spyOn(AgentProfilesService, "getProfile").mockRejectedValue(
        new Error("boom"),
      );
      const toastSpy = vi
        .spyOn(toasts, "displayErrorToast")
        .mockImplementation(() => {});

      // Act & Assert
      await expect(
        repointAgentProfileMcpRefs("shttp", "sonarqube"),
      ).resolves.toBeUndefined();
      expect(toastSpy).toHaveBeenCalledTimes(1);
    });
  });

  describe("useTestMcpServer", () => {
    it("tests an edited server with its stored key and unchanged redacted credential", async () => {
      // Arrange: the settings round-trip returns the stored credential
      // redacted; the form echoes it back unchanged.
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            jira: {
              url: "https://mcp-jira.example.com/mcp",
              headers: { Authorization: "**********" },
            },
          },
        },
      } as unknown as Settings);
      const testSpy = vi
        .spyOn(McpService, "testServer")
        .mockResolvedValue({ ok: true, tools: ["search_issues"] });
      const { result } = renderHook(() => useTestMcpServer(), {
        wrapper: createWrapper(),
      });

      // Act
      result.current.mutate({
        serverId: "shttp-0",
        server: {
          type: "shttp",
          url: "https://mcp-jira.example.com/mcp",
          api_key: "**********",
        },
      });

      // Assert: the request names the stored server and carries the
      // redacted credential in the same shape a save would persist, so
      // the backend can restore the real value.
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(testSpy).toHaveBeenCalledWith({
        name: "jira",
        server: {
          url: "https://mcp-jira.example.com/mcp",
          headers: { Authorization: "**********" },
        },
      });
      expect(result.current.data).toEqual({
        ok: true,
        tools: ["search_issues"],
      });
    });

    it("tests a new remote server with the typed key as a bearer credential", async () => {
      const getSettingsSpy = vi.spyOn(SettingsService, "getSettings");
      const testSpy = vi
        .spyOn(McpService, "testServer")
        .mockResolvedValue({ ok: true, tools: [] });
      const { result } = renderHook(() => useTestMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        server: {
          type: "sse",
          url: "https://mcp.example.com/sse",
          api_key: "plain-key",
        },
      });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(getSettingsSpy).not.toHaveBeenCalled();
      expect(testSpy).toHaveBeenCalledWith({
        name: "sse",
        server: {
          url: "https://mcp.example.com/sse",
          transport: "sse",
          auth: { strategy: "bearer", value: "plain-key" },
        },
      });
    });

    it("tests an edited server without credentials when the key was removed", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: {
          mcp_config: {
            jira: {
              url: "https://mcp-jira.example.com/mcp",
              headers: { Authorization: "**********" },
            },
          },
        },
      } as unknown as Settings);
      const testSpy = vi
        .spyOn(McpService, "testServer")
        .mockResolvedValue({ ok: false, error: "401", error_kind: "unknown" });
      const { result } = renderHook(() => useTestMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({
        serverId: "shttp-0",
        server: { type: "shttp", url: "https://mcp-jira.example.com/mcp" },
      });

      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      // Mirrors the save payload: the stored credential is explicitly cleared
      // rather than silently re-applied by the backend.
      expect(testSpy).toHaveBeenCalledWith({
        name: "jira",
        server: expect.objectContaining({
          url: "https://mcp-jira.example.com/mcp",
          auth: null,
        }),
      });
    });
  });

  describe("error handling", () => {
    it("handles getSettings failure", async () => {
      vi.spyOn(SettingsService, "getSettings").mockRejectedValue(
        new Error("Network error"),
      );

      const { result } = renderHook(() => useAddMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({ type: "sse", url: "https://server.com" });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(result.current.error).toBeDefined();
    });

    it("handles saveSettings failure", async () => {
      vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
        agent_settings: { mcp_config: null },
      } as any);

      vi.spyOn(SettingsService, "saveSettings").mockRejectedValue(
        new Error("Save failed"),
      );

      const { result } = renderHook(() => useAddMcpServer(), {
        wrapper: createWrapper(),
      });

      result.current.mutate({ type: "sse", url: "https://server.com" });

      await waitFor(() => {
        expect(result.current.isError).toBe(true);
      });

      expect(result.current.error).toBeDefined();
    });
  });
});
