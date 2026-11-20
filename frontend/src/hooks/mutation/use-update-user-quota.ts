import { useMutation, useQueryClient } from "@tanstack/react-query";
import { quotaService } from "#/api/quota-service/quota-service.api";
import { QUOTA_QUERY_KEYS } from "#/hooks/query/use-quota-status";
import { SUPER_ADMIN_QUERY_KEYS } from "#/hooks/query/use-super-admin";

export const useUpdateUserQuota = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: quotaService.updateUserQuota,
    onSuccess: (data) => {
      queryClient.setQueryData(QUOTA_QUERY_KEYS.user(data.user_id), data);
      queryClient.invalidateQueries({
        queryKey: QUOTA_QUERY_KEYS.user(data.user_id),
      });
      queryClient.invalidateQueries({ queryKey: QUOTA_QUERY_KEYS.status });
      queryClient.invalidateQueries({ queryKey: SUPER_ADMIN_QUERY_KEYS.users });
    },
    meta: { disableToast: true },
  });
};
