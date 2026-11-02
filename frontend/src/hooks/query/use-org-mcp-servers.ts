import { useQuery } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useConfig } from "./use-config";
import { useIsAuthed } from "#/hooks/query/use-is-authed";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";

export const ORG_MCP_SERVERS_QUERY_KEY = "org-mcp-servers";

/**
 * The organization's MCP server listings (OHE-3091), credentials masked.
 * Off unless the `enable_org_mcp_servers` flag is on, so installs without the
 * feature make no extra request.
 */
export const useOrgMcpServers = () => {
  const { data: config } = useConfig();
  const { data: isAuthed } = useIsAuthed();
  const { organizationId } = useSelectedOrganizationId();

  const isOss = config?.app_mode === "oss";
  const enabled =
    !!config?.feature_flags?.enable_org_mcp_servers &&
    !isOss &&
    !!isAuthed &&
    !!organizationId;

  const query = useQuery({
    queryKey: [ORG_MCP_SERVERS_QUERY_KEY, organizationId],
    queryFn: () => organizationService.getOrgMcpServers(organizationId!),
    enabled,
    staleTime: 1000 * 60 * 5, // 5 minutes
    gcTime: 1000 * 60 * 15, // 15 minutes
  });

  return {
    data: query.data?.items ?? [],
    isLoading: enabled && query.isLoading,
    isError: query.isError,
  };
};
