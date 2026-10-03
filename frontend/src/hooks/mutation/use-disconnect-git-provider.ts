import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import UserService from "#/api/user-service/user-service.api";
import { SETTINGS_QUERY_KEYS } from "#/hooks/query/query-keys";
import { Provider } from "#/types/settings";

export const useDisconnectGitProvider = () => {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: (provider: Provider) =>
      UserService.disconnectGitProvider(provider),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: SETTINGS_QUERY_KEYS.personal(organizationId),
      });
    },
    meta: {
      disableToast: true,
    },
  });
};
