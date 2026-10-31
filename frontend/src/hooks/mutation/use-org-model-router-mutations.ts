import { useMutation, useQueryClient } from "@tanstack/react-query";
import OrgModelRoutersService, {
  type MetaProfile,
  type UpdateModelRouterSettingsRequest,
} from "#/api/organization-service/org-model-routers-service.api";
import { MODEL_ROUTERS_QUERY_KEYS } from "#/hooks/query/query-keys";

// Every mutation changes the list (names, active pointer or the run-at-start
// toggle), so each one refreshes the org's router queries.
const useInvalidateModelRouters = (orgId: string | null | undefined) => {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({
      queryKey: MODEL_ROUTERS_QUERY_KEYS.byOrg(orgId),
    });
};

interface SaveModelRouterVariables {
  name: string;
  config: MetaProfile;
}

export function useSaveOrgModelRouter(orgId: string | null | undefined) {
  const invalidate = useInvalidateModelRouters(orgId);

  return useMutation({
    mutationFn: ({ name, config }: SaveModelRouterVariables) => {
      if (!orgId) throw new Error("Organization ID is required");
      return OrgModelRoutersService.save(orgId, name, config);
    },
    onSuccess: invalidate,
    meta: { disableToast: true },
  });
}

export function useDeleteOrgModelRouter(orgId: string | null | undefined) {
  const invalidate = useInvalidateModelRouters(orgId);

  return useMutation({
    mutationFn: (name: string) => {
      if (!orgId) throw new Error("Organization ID is required");
      return OrgModelRoutersService.delete(orgId, name);
    },
    onSuccess: invalidate,
    meta: { disableToast: true },
  });
}

export function useActivateOrgModelRouter(orgId: string | null | undefined) {
  const invalidate = useInvalidateModelRouters(orgId);

  return useMutation({
    mutationFn: (name: string) => {
      if (!orgId) throw new Error("Organization ID is required");
      return OrgModelRoutersService.activate(orgId, name);
    },
    onSuccess: invalidate,
    meta: { disableToast: true },
  });
}

export function useDeactivateOrgModelRouter(orgId: string | null | undefined) {
  const invalidate = useInvalidateModelRouters(orgId);

  return useMutation({
    mutationFn: (name: string) => {
      if (!orgId) throw new Error("Organization ID is required");
      return OrgModelRoutersService.deactivate(orgId, name);
    },
    onSuccess: invalidate,
    meta: { disableToast: true },
  });
}

export function useUpdateOrgModelRouterSettings(
  orgId: string | null | undefined,
) {
  const invalidate = useInvalidateModelRouters(orgId);

  return useMutation({
    mutationFn: (request: UpdateModelRouterSettingsRequest) => {
      if (!orgId) throw new Error("Organization ID is required");
      return OrgModelRoutersService.updateSettings(orgId, request);
    },
    onSuccess: invalidate,
    meta: { disableToast: true },
  });
}
