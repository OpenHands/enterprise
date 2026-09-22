import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  hubIntegrationFromUserRequest,
  unusedWindowMs,
} from "#/components/features/integrations-hub/hub-format";
import { formatPermissionProfileSummary } from "#/components/features/integrations-hub/permission-profile-utils";
import {
  mockApiKeys,
  mockApprovals,
  mockCatalogIntegrations,
  mockDuplicateGroups,
  mockIntegrationsForWorkspace,
  mockOverviewUsers,
  mockPermissionProfiles,
  mockRequestableCatalog,
  mockUserRequests,
} from "#/components/features/integrations-hub/integrations-hub-mock";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";
import type {
  HubApproval,
  HubCustomMcpInput,
  HubIntegration,
  HubIntegrationRequestPayload,
  HubPermissionProfile,
  HubPermissionProfileSnapshot,
  HubToolAccessMode,
  HubUserRequest,
  IntegrationsHubViewModel,
} from "#/types/integrations-hub";

export const IntegrationsHubStubContext =
  createContext<IntegrationsHubViewModel | null>(null);

function catalogItem(slug: string): HubIntegration | undefined {
  return mockCatalogIntegrations().find((item) => item.slug === slug);
}

/**
 * STUB: local Hub list for mock / VITE_MOCK_API.
 * Live path uses TanStack Query against /api/integrations-hub.
 *
 * Legacy cutover reconnect items are derived on the client from
 * settings.provider_tokens_set (see useIntegrationsHubCutover) until
 * the Hub API exposes an explicit legacy-connections payload.
 */
function useIntegrationsHubStubState(): IntegrationsHubViewModel {
  const { isPersonalOrg } = useOrgTypeAndAccess();
  const isPersonalWorkspace = isPersonalOrg === true;
  const [integrations, setIntegrations] = useState<HubIntegration[]>(() =>
    mockIntegrationsForWorkspace(isPersonalWorkspace),
  );
  const [approvals, setApprovals] = useState<HubApproval[]>(mockApprovals);
  const [userRequests, setUserRequests] =
    useState<HubUserRequest[]>(mockUserRequests);
  const [permissionProfiles, setPermissionProfiles] = useState<
    HubPermissionProfile[]
  >(mockPermissionProfiles);
  const [extraCatalog, setExtraCatalog] = useState<HubIntegration[]>([]);

  useEffect(() => {
    setIntegrations(mockIntegrationsForWorkspace(isPersonalWorkspace));
  }, [isPersonalWorkspace]);

  const connect = useCallback((slug: string, seed?: HubIntegration) => {
    const nextItem = catalogItem(slug) ?? seed;
    setIntegrations((current) => {
      const existing = current.find((item) => item.slug === slug);
      if (existing) {
        return current.map((item) =>
          item.slug === slug
            ? { ...item, connected: true, enabled: true }
            : item,
        );
      }
      return nextItem
        ? [...current, { ...nextItem, connected: true, enabled: true }]
        : current;
    });
    if (seed && !catalogItem(slug)) {
      setExtraCatalog((current) =>
        current.some((item) => item.slug === seed.slug)
          ? current
          : [...current, { ...seed, connected: true, enabled: true }],
      );
    }
  }, []);

  const disconnect = useCallback((slug: string) => {
    setIntegrations((current) =>
      current.map((item) =>
        item.slug === slug
          ? { ...item, connected: false, enabled: false }
          : item,
      ),
    );
  }, []);

  const toggleEnabled = useCallback((slug: string) => {
    setIntegrations((current) =>
      current.map((item) =>
        item.slug === slug ? { ...item, enabled: !item.enabled } : item,
      ),
    );
  }, []);

  const updateToolAccess = useCallback(
    (slug: string, toolName: string, mode: HubToolAccessMode) => {
      setIntegrations((current) =>
        current.map((item) =>
          item.slug === slug
            ? {
                ...item,
                tools: item.tools.map((tool) =>
                  tool.name === toolName ? { ...tool, accessMode: mode } : tool,
                ),
              }
            : item,
        ),
      );
    },
    [],
  );

  const requestIntegration = useCallback(
    (payload: HubIntegrationRequestPayload) => {
      const createdAt = new Date().toISOString();
      if (payload.source === "catalog") {
        const slugs = payload.slugs ?? [];
        setUserRequests((current) => [
          ...current,
          ...slugs.map((slug) => {
            const item = catalogItem(slug);
            return {
              id: `req-${slug}-${createdAt}`,
              name: item?.name ?? slug,
              slug,
              requestedBy: "you",
              notes: payload.notes,
              source: "catalog" as const,
              createdAt,
            };
          }),
        ]);
        return;
      }
      setUserRequests((current) => [
        ...current,
        {
          id: `req-custom-${createdAt}`,
          name: payload.name?.trim() || "Custom integration",
          slug: payload.name?.trim().toLowerCase().replace(/\s+/g, "-") ?? "",
          requestedBy: "you",
          notes: payload.notes,
          description: payload.description?.trim() || undefined,
          docsUrl: payload.docsUrl?.trim() || undefined,
          source: "custom",
          createdAt,
        },
      ]);
    },
    [],
  );

  const decideApprovals = useCallback(
    (ids: string[], status: "approved" | "denied", durationHours?: number) => {
      const decidedAt = new Date().toISOString();
      setApprovals((current) =>
        current.map((item) =>
          ids.includes(item.id)
            ? {
                ...item,
                status,
                decidedBy: "you",
                decidedAt,
                requestedMinutes:
                  status === "approved" && durationHours
                    ? durationHours * 60
                    : item.requestedMinutes,
              }
            : item,
        ),
      );
    },
    [],
  );

  const dismissUserRequest = useCallback((id: string) => {
    setUserRequests((current) => current.filter((item) => item.id !== id));
  }, []);

  const fulfillUserRequest = useCallback(
    (id: string) => {
      setUserRequests((current) => {
        const request = current.find((item) => item.id === id);
        if (request?.slug) {
          connect(request.slug, hubIntegrationFromUserRequest(request));
        }
        return current.filter((item) => item.id !== id);
      });
    },
    [connect],
  );

  const registerCustomMcp = useCallback((input: HubCustomMcpInput) => {
    const next: HubIntegration = {
      slug: input.slug,
      name: input.name,
      description: `Custom MCP server at ${input.serverUrl}`,
      authStrategy: input.authStrategy === "api_key" ? "api_key" : "oauth2",
      toolCount: 0,
      provider: "Custom",
      kind: "Custom",
      tools: [],
      connected: true,
      enabled: true,
    };
    setExtraCatalog((current) => [...current, next]);
    setIntegrations((current) =>
      current.some((item) => item.slug === next.slug)
        ? current
        : [...current, next],
    );
  }, []);

  const addPermissionProfile = useCallback(
    (name: string, snapshot?: HubPermissionProfileSnapshot) => {
      setPermissionProfiles((current) => [
        ...current,
        {
          id: `profile-${current.length + 1}`,
          name,
          summary: snapshot
            ? formatPermissionProfileSummary(snapshot, integrations)
            : "0 tools enabled",
          updatedAt: new Date().toISOString(),
          agentApiKey: `ohk_prof_${current.length + 1}`,
          snapshot,
        },
      ]);
    },
    [integrations],
  );

  const savePermissionProfileSnapshot = useCallback(
    (id: string, snapshot: HubPermissionProfileSnapshot) => {
      setPermissionProfiles((current) =>
        current.map((profile) =>
          profile.id === id
            ? {
                ...profile,
                snapshot,
                summary: formatPermissionProfileSummary(snapshot, integrations),
                updatedAt: new Date().toISOString(),
              }
            : profile,
        ),
      );
    },
    [integrations],
  );

  const setDefaultPermissionProfile = useCallback((id: string) => {
    setPermissionProfiles((current) =>
      current.map((profile) => ({
        ...profile,
        isDefault: profile.id === id,
      })),
    );
  }, []);

  const deletePermissionProfile = useCallback((id: string) => {
    setPermissionProfiles((current) =>
      current.filter((profile) => profile.id !== id || profile.isDefault),
    );
  }, []);

  const disableUnusedTools = useCallback(
    (thresholdValue: number, thresholdUnit: "days" | "weeks" | "months") => {
      const value = Math.min(Math.max(thresholdValue, 1), 3650);
      const cutoff = Date.now() - unusedWindowMs(value, thresholdUnit);
      setIntegrations((current) =>
        current.map((item) => ({
          ...item,
          tools: item.tools.map((tool) =>
            tool.accessMode !== "disabled" &&
            tool.lastUsedAt &&
            new Date(tool.lastUsedAt).getTime() < cutoff
              ? { ...tool, accessMode: "disabled" as const }
              : tool,
          ),
        })),
      );
    },
    [],
  );

  const catalogIntegrations = useMemo(
    () =>
      isPersonalWorkspace
        ? [
            ...integrations,
            ...extraCatalog.filter(
              (item) => !integrations.some((row) => row.slug === item.slug),
            ),
          ]
        : [...mockCatalogIntegrations(), ...extraCatalog].map((item) => {
            const local = integrations.find((row) => row.slug === item.slug);
            if (!local) {
              return item;
            }
            return {
              ...item,
              connected: local.connected,
              enabled: local.enabled,
              tools: local.tools.length > 0 ? local.tools : item.tools,
              toolCount: local.tools.length || item.toolCount,
            };
          }),
    [extraCatalog, integrations, isPersonalWorkspace],
  );

  const requestableCatalog = useMemo(
    () =>
      isPersonalWorkspace
        ? []
        : mockRequestableCatalog().filter(
            (item) =>
              !integrations.some(
                (row) => row.slug === item.slug && row.connected,
              ),
          ),
    [integrations, isPersonalWorkspace],
  );

  return {
    isPersonalWorkspace,
    showRequestButton: !isPersonalWorkspace,
    integrations,
    catalogIntegrations,
    requestableCatalog,
    approvals,
    userRequests,
    apiKeys: mockApiKeys(),
    permissionProfiles,
    overviewUsers: mockOverviewUsers(),
    duplicateGroups: mockDuplicateGroups(),
    connect,
    disconnect,
    toggleEnabled,
    updateToolAccess,
    requestIntegration,
    decideApprovals,
    dismissUserRequest,
    fulfillUserRequest,
    registerCustomMcp,
    addPermissionProfile,
    savePermissionProfileSnapshot,
    setDefaultPermissionProfile,
    deletePermissionProfile,
    disableUnusedTools,
  };
}

export function IntegrationsHubStubProvider({
  children,
}: {
  children: ReactNode;
}) {
  const value = useIntegrationsHubStubState();
  return createElement(
    IntegrationsHubStubContext.Provider,
    { value },
    children,
  );
}

export function useIntegrationsHubStub(): IntegrationsHubViewModel {
  const context = useContext(IntegrationsHubStubContext);
  if (!context) {
    throw new Error(
      "useIntegrationsHubStub must be used within IntegrationsHubStubProvider",
    );
  }
  return context;
}

export type IntegrationsHubStubViewModel = ReturnType<
  typeof useIntegrationsHubStub
>;
