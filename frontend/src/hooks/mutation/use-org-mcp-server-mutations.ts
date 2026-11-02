import { useMutation, useQueryClient } from "@tanstack/react-query";
import McpService from "#/api/mcp-service/mcp-service.api";
import {
  OrgMcpServerCreate,
  OrgMcpServerUpdate,
} from "#/api/org-mcp-servers.types";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { ORG_MCP_SERVERS_QUERY_KEY } from "#/hooks/query/use-org-mcp-servers";

function useInvalidateOrgMcpServers() {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();
  return () =>
    queryClient.invalidateQueries({
      queryKey: [ORG_MCP_SERVERS_QUERY_KEY, organizationId],
    });
}

function requireOrganizationId(organizationId: string | null | undefined) {
  if (!organizationId) {
    throw new Error("An organization is required to manage its MCP servers.");
  }
  return organizationId;
}

export function useCreateOrgMcpServer() {
  const { organizationId } = useSelectedOrganizationId();
  const invalidate = useInvalidateOrgMcpServers();
  return useMutation({
    mutationFn: (payload: OrgMcpServerCreate) =>
      organizationService.createOrgMcpServer(
        requireOrganizationId(organizationId),
        payload,
      ),
    onSuccess: invalidate,
  });
}

export function useUpdateOrgMcpServer() {
  const { organizationId } = useSelectedOrganizationId();
  const invalidate = useInvalidateOrgMcpServers();
  return useMutation({
    mutationFn: ({
      name,
      payload,
    }: {
      name: string;
      payload: OrgMcpServerUpdate;
    }) =>
      organizationService.updateOrgMcpServer(
        requireOrganizationId(organizationId),
        name,
        payload,
      ),
    onSuccess: invalidate,
  });
}

export function useDeleteOrgMcpServer() {
  const { organizationId } = useSelectedOrganizationId();
  const invalidate = useInvalidateOrgMcpServers();
  return useMutation({
    mutationFn: (name: string) =>
      organizationService.deleteOrgMcpServer(
        requireOrganizationId(organizationId),
        name,
      ),
    onSuccess: invalidate,
  });
}

/**
 * Probe an organization listing through `POST /api/v1/mcp/test`. The backend
 * restores a masked credential from the stored listing of the same name and
 * expands `${NAME}` references from the caller's secrets.
 */
export function useTestOrgMcpServer() {
  return useMutation({
    mutationFn: ({
      name,
      server,
    }: {
      name: string;
      server: Record<string, unknown>;
    }) => McpService.testServer({ name, server }),
  });
}
