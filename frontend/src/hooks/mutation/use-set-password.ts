import { useMutation, useQueryClient } from "@tanstack/react-query";
import { idpService } from "#/api/idp-service/idp-service.api";
import { SetPasswordParams } from "#/api/idp-service/idp.types";
import { QUERY_KEYS } from "#/hooks/query/query-keys";

/** Set (first time) or change the current user's dev-IDP password. */
export const useSetPassword = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (params: SetPasswordParams) => idpService.setPassword(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.IDP_HAS_PASSWORD });
    },
  });
};
