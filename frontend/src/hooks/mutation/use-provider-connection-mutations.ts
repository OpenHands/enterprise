import { useMutation, useQueryClient } from "@tanstack/react-query";
import OrgProviderConnectionsService, {
  type CreateProviderConnectionRequest,
  type UpdateProviderConnectionRequest,
} from "#/api/organization-service/org-provider-connections-service.api";
import { LLM_PROFILES_QUERY_KEY } from "#/hooks/query/use-llm-profiles";
import { ORG_LLM_PROFILES_QUERY_KEY } from "#/hooks/query/use-org-llm-profiles";
import { PROVIDER_CONNECTIONS_QUERY_KEYS } from "#/hooks/query/query-keys";

// Linked profiles report the connection's key presence via `api_key_set`, so
// refresh the profile lists too after a rotation, rename, or delete.
const invalidateProfileCaches = (
  queryClient: ReturnType<typeof useQueryClient>,
  orgId: string | null | undefined,
) => {
  queryClient.invalidateQueries({
    queryKey: [ORG_LLM_PROFILES_QUERY_KEY, orgId],
  });
  queryClient.invalidateQueries({
    queryKey: [LLM_PROFILES_QUERY_KEY],
  });
};

export function useCreateProviderConnection(orgId: string | null | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (request: CreateProviderConnectionRequest) => {
      if (!orgId) throw new Error("Organization ID is required");
      return OrgProviderConnectionsService.create(orgId, request);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: PROVIDER_CONNECTIONS_QUERY_KEYS.all,
      });
    },
    // Consumers handle errors with try-catch and manual toasts.
    meta: { disableToast: true },
  });
}

interface UpdateProviderConnectionVariables {
  id: string;
  request: UpdateProviderConnectionRequest;
}

export function useUpdateProviderConnection(orgId: string | null | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, request }: UpdateProviderConnectionVariables) => {
      if (!orgId) throw new Error("Organization ID is required");
      return OrgProviderConnectionsService.update(orgId, id, request);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: PROVIDER_CONNECTIONS_QUERY_KEYS.all,
      });
      invalidateProfileCaches(queryClient, orgId);
    },
    meta: { disableToast: true },
  });
}

export function useDeleteProviderConnection(orgId: string | null | undefined) {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => {
      if (!orgId) throw new Error("Organization ID is required");
      return OrgProviderConnectionsService.delete(orgId, id);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: PROVIDER_CONNECTIONS_QUERY_KEYS.all,
      });
      invalidateProfileCaches(queryClient, orgId);
    },
    meta: { disableToast: true },
  });
}
