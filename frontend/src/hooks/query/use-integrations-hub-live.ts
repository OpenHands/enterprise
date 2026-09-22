import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  accessRequestToHubApproval,
  hubAccessModeToApi,
  integrationRequestToHubUserRequest,
  integrationSpecToHubIntegration,
  keyStatusToHubApiKey,
  managedConnectorToHubIntegration,
  overviewToHub,
  permissionProfileToHub,
} from "#/api/integrations-hub/integrations-hub-adapters";
import { integrationsHubService } from "#/api/integrations-hub/integrations-hub-service.api";
import type { HubApiManagedConnector } from "#/api/integrations-hub/integrations-hub.types";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";
import type {
  HubCustomMcpInput,
  HubIntegration,
  HubIntegrationRequestPayload,
  HubPermissionProfileSnapshot,
  HubToolAccessMode,
  IntegrationsHubViewModel,
} from "#/types/integrations-hub";

export const INTEGRATIONS_HUB_QUERY_KEYS = {
  all: ["integrations-hub"] as const,
  integrations: () => ["integrations-hub", "integrations"] as const,
  catalog: (filter: string) => ["integrations-hub", "catalog", filter] as const,
  approvals: () => ["integrations-hub", "approvals"] as const,
  userRequests: () => ["integrations-hub", "user-requests"] as const,
  profiles: () => ["integrations-hub", "profiles"] as const,
  overview: () => ["integrations-hub", "overview"] as const,
  keys: () => ["integrations-hub", "keys"] as const,
  adminConnectors: () => ["integrations-hub", "admin-connectors"] as const,
} as const;

export const IntegrationsHubLiveContext =
  createContext<IntegrationsHubViewModel | null>(null);

function isManagedConnectorList(
  value: unknown,
): value is HubApiManagedConnector[] {
  return (
    Array.isArray(value) &&
    value.every(
      (item) =>
        item &&
        typeof item === "object" &&
        "slug" in item &&
        typeof (item as HubApiManagedConnector).slug === "string",
    )
  );
}

function useIntegrationsHubLiveState(): IntegrationsHubViewModel {
  const queryClient = useQueryClient();
  const { isPersonalOrg } = useOrgTypeAndAccess();
  const isPersonalWorkspace = isPersonalOrg === true;

  const integrationsQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.integrations(),
    queryFn: () => integrationsHubService.listIntegrations(),
  });

  const catalogQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.catalog(
      isPersonalWorkspace ? "enabled" : "enabled",
    ),
    queryFn: () => integrationsHubService.listCatalog("enabled"),
  });

  const registerableQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.catalog("registerable"),
    queryFn: () => integrationsHubService.listCatalog("registerable"),
    enabled: !isPersonalWorkspace,
  });

  const approvalsQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.approvals(),
    queryFn: () => integrationsHubService.listApprovals(),
  });

  const userRequestsQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.userRequests(),
    queryFn: () => integrationsHubService.listUserRequests(),
    enabled: !isPersonalWorkspace,
  });

  const profilesQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.profiles(),
    queryFn: () => integrationsHubService.listPermissionProfiles(),
  });

  const overviewQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.overview(),
    queryFn: () => integrationsHubService.getOverview(),
    enabled: !isPersonalWorkspace,
  });

  const keysQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.keys(),
    queryFn: async () => {
      const [userKey, agentKey] = await Promise.all([
        integrationsHubService.getUserKey(),
        integrationsHubService.getUserAgentKey(),
      ]);
      return { userKey, agentKey };
    },
  });

  const adminConnectorsQuery = useQuery({
    queryKey: INTEGRATIONS_HUB_QUERY_KEYS.adminConnectors(),
    queryFn: () => integrationsHubService.listAdminConnectors(),
    enabled: !isPersonalWorkspace,
  });

  const invalidateHub = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: INTEGRATIONS_HUB_QUERY_KEYS.all,
    });
  }, [queryClient]);

  const connectMutation = useMutation({
    mutationFn: async ({
      slug,
      seed,
    }: {
      slug: string;
      seed?: HubIntegration;
    }) => {
      if (seed?.authStrategy === "oauth2" || !seed) {
        const oauth = await integrationsHubService.startOAuth(slug);
        const redirectUrl = oauth.redirectUrl ?? oauth.url;
        if (redirectUrl) {
          window.location.assign(redirectUrl);
          return;
        }
      }
      await integrationsHubService.createIntegration({
        key: slug,
        name: seed?.name ?? slug,
        kind: seed?.kind ?? "Custom",
        provider: seed?.provider ?? "Custom",
        authStrategy: seed?.authStrategy ?? "api_key",
        enabled: true,
        tools: Object.fromEntries(
          (seed?.tools ?? []).map((tool) => [
            tool.name,
            {
              name: tool.name,
              description: tool.description,
              accessMode: hubAccessModeToApi(tool.accessMode),
              defaultScopes: tool.defaultScopes,
            },
          ]),
        ),
        config: {
          description: seed?.description,
          serverUrl: seed?.serverUrl,
          apiBaseUrl: seed?.apiBaseUrl,
          openApiUrl: seed?.openApiUrl,
        },
      });
    },
    onSuccess: invalidateHub,
  });

  const disconnectMutation = useMutation({
    mutationFn: (slug: string) =>
      integrationsHubService.deleteIntegration(slug),
    onSuccess: invalidateHub,
  });

  const toggleMutation = useMutation({
    mutationFn: (slug: string) =>
      integrationsHubService.toggleIntegration(slug),
    onSuccess: invalidateHub,
  });

  const toolAccessMutation = useMutation({
    mutationFn: ({
      slug,
      toolName,
      mode,
    }: {
      slug: string;
      toolName: string;
      mode: HubToolAccessMode;
    }) =>
      integrationsHubService.patchToolAccess(
        slug,
        toolName,
        hubAccessModeToApi(mode),
      ),
    onSuccess: invalidateHub,
  });

  const requestMutation = useMutation({
    mutationFn: (payload: HubIntegrationRequestPayload) =>
      integrationsHubService.requestIntegration(payload),
    onSuccess: invalidateHub,
  });

  const decideApprovalsMutation = useMutation({
    mutationFn: async ({
      ids,
      status,
      durationHours,
    }: {
      ids: string[];
      status: "approved" | "denied";
      durationHours?: number;
    }) => {
      await Promise.all(
        ids.map((id) =>
          status === "approved"
            ? integrationsHubService.approveAccessRequest(
                id,
                durationHours != null ? durationHours * 60 : undefined,
              )
            : integrationsHubService.rejectAccessRequest(id),
        ),
      );
    },
    onSuccess: invalidateHub,
  });

  const dismissRequestMutation = useMutation({
    mutationFn: (id: string) => integrationsHubService.dismissUserRequest(id),
    onSuccess: invalidateHub,
  });

  const fulfillRequestMutation = useMutation({
    mutationFn: (id: string) => integrationsHubService.fulfillUserRequest(id),
    onSuccess: invalidateHub,
  });

  const registerMcpMutation = useMutation({
    mutationFn: (input: HubCustomMcpInput) =>
      integrationsHubService.registerCustomMcp(input),
    onSuccess: invalidateHub,
  });

  const addProfileMutation = useMutation({
    mutationFn: ({
      name,
      snapshot,
    }: {
      name: string;
      snapshot?: HubPermissionProfileSnapshot;
    }) =>
      integrationsHubService.createPermissionProfile({
        name,
        snapshot,
      }),
    onSuccess: invalidateHub,
  });

  const saveProfileMutation = useMutation({
    mutationFn: ({
      id,
      snapshot,
    }: {
      id: string;
      snapshot: HubPermissionProfileSnapshot;
    }) => integrationsHubService.patchPermissionProfile(id, snapshot),
    onSuccess: invalidateHub,
  });

  const setDefaultProfileMutation = useMutation({
    mutationFn: (id: string) =>
      integrationsHubService.loadPermissionProfile(id),
    onSuccess: invalidateHub,
  });

  const deleteProfileMutation = useMutation({
    mutationFn: (id: string) =>
      integrationsHubService.deletePermissionProfile(id),
    onSuccess: invalidateHub,
  });

  const disableUnusedMutation = useMutation({
    mutationFn: ({
      thresholdValue,
      thresholdUnit,
    }: {
      thresholdValue: number;
      thresholdUnit: "days" | "weeks" | "months";
    }) =>
      integrationsHubService.disableUnusedTools({
        thresholdValue,
        thresholdUnit,
      }),
    onSuccess: invalidateHub,
  });

  const installed = useMemo(
    () =>
      (integrationsQuery.data ?? []).map((spec) =>
        integrationSpecToHubIntegration(spec, { connected: true }),
      ),
    [integrationsQuery.data],
  );

  const installedBySlug = useMemo(() => {
    const map = new Map(
      (integrationsQuery.data ?? []).map((spec) => [spec.key, spec]),
    );
    return map;
  }, [integrationsQuery.data]);

  const catalogIntegrations = useMemo(() => {
    const catalogData = catalogQuery.data;
    if (isManagedConnectorList(catalogData)) {
      return catalogData.map((connector) =>
        managedConnectorToHubIntegration(
          connector,
          installedBySlug.get(connector.slug),
        ),
      );
    }
    if (Array.isArray(catalogData)) {
      return catalogData.map((spec) =>
        integrationSpecToHubIntegration(spec, {
          connected: installedBySlug.has(spec.key),
        }),
      );
    }
    const adminConnectors = adminConnectorsQuery.data;
    if (adminConnectors) {
      return adminConnectors.map((connector) =>
        managedConnectorToHubIntegration(
          connector,
          installedBySlug.get(connector.slug),
        ),
      );
    }
    return installed;
  }, [
    adminConnectorsQuery.data,
    catalogQuery.data,
    installed,
    installedBySlug,
  ]);

  const requestableCatalog = useMemo(() => {
    const { data } = registerableQuery;
    if (isManagedConnectorList(data)) {
      return data.map((connector) =>
        managedConnectorToHubIntegration(connector),
      );
    }
    if (Array.isArray(data)) {
      return data.map((spec) =>
        integrationSpecToHubIntegration(spec, { connected: false }),
      );
    }
    return [];
  }, [registerableQuery.data]);

  const approvals = useMemo(
    () =>
      (approvalsQuery.data ?? []).map((request) =>
        accessRequestToHubApproval(
          request,
          installedBySlug.get(request.integrationKey)?.name,
        ),
      ),
    [approvalsQuery.data, installedBySlug],
  );

  const userRequests = useMemo(
    () =>
      (userRequestsQuery.data ?? []).map(integrationRequestToHubUserRequest),
    [userRequestsQuery.data],
  );

  const permissionProfiles = useMemo(() => {
    const profiles = profilesQuery.data ?? [];
    return profiles.map((profile, index) =>
      permissionProfileToHub(profile, { isDefault: index === 0 }),
    );
  }, [profilesQuery.data]);

  const apiKeys = useMemo(() => {
    const keys = [];
    const userKey = keysQuery.data
      ? keyStatusToHubApiKey(keysQuery.data.userKey, "User automation key")
      : null;
    const agentKey = keysQuery.data
      ? keyStatusToHubApiKey(keysQuery.data.agentKey, "Agent runtime key")
      : null;
    if (userKey) {
      keys.push(userKey);
    }
    if (agentKey) {
      keys.push(agentKey);
    }
    return keys;
  }, [keysQuery.data]);

  const { overviewUsers, duplicateGroups } = useMemo(
    () => overviewToHub(overviewQuery.data ?? {}),
    [overviewQuery.data],
  );

  return {
    isPersonalWorkspace,
    showRequestButton: !isPersonalWorkspace,
    integrations: installed,
    catalogIntegrations,
    requestableCatalog,
    approvals,
    userRequests,
    apiKeys,
    permissionProfiles,
    overviewUsers,
    duplicateGroups,
    connect: (slug, seed) => {
      connectMutation.mutate({ slug, seed });
    },
    disconnect: (slug) => {
      disconnectMutation.mutate(slug);
    },
    toggleEnabled: (slug) => {
      toggleMutation.mutate(slug);
    },
    updateToolAccess: (slug, toolName, mode) => {
      toolAccessMutation.mutate({ slug, toolName, mode });
    },
    requestIntegration: (payload) => {
      requestMutation.mutate(payload);
    },
    decideApprovals: (ids, status, durationHours) => {
      decideApprovalsMutation.mutate({ ids, status, durationHours });
    },
    dismissUserRequest: (id) => {
      dismissRequestMutation.mutate(id);
    },
    fulfillUserRequest: (id) => {
      fulfillRequestMutation.mutate(id);
    },
    registerCustomMcp: (input) => {
      registerMcpMutation.mutate(input);
    },
    addPermissionProfile: (name, snapshot) => {
      addProfileMutation.mutate({ name, snapshot });
    },
    savePermissionProfileSnapshot: (id, snapshot) => {
      saveProfileMutation.mutate({ id, snapshot });
    },
    setDefaultPermissionProfile: (id) => {
      setDefaultProfileMutation.mutate(id);
    },
    deletePermissionProfile: (id) => {
      deleteProfileMutation.mutate(id);
    },
    disableUnusedTools: (thresholdValue, thresholdUnit) => {
      disableUnusedMutation.mutate({ thresholdValue, thresholdUnit });
    },
  };
}

export function IntegrationsHubLiveProvider({
  children,
}: {
  children: ReactNode;
}) {
  const value = useIntegrationsHubLiveState();
  return createElement(
    IntegrationsHubLiveContext.Provider,
    { value },
    children,
  );
}

export function useIntegrationsHubLive(): IntegrationsHubViewModel {
  const context = useContext(IntegrationsHubLiveContext);
  if (!context) {
    throw new Error(
      "useIntegrationsHubLive must be used within IntegrationsHubLiveProvider",
    );
  }
  return context;
}
