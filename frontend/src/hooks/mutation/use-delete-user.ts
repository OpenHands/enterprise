import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { adminService } from "#/api/admin-service/admin-service.api";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

/**
 * Permanently delete a user account via the instance-wide "Remove" action
 * in the "All Users" view (``DELETE /api/admin/users/{user_id}``).
 * Counterpart to ``useRemoveMember``, which only removes one org
 * membership rather than the account itself.
 */
export const useDeleteUser = () => {
  const queryClient = useQueryClient();
  const { t } = useTranslation();

  return useMutation({
    mutationFn: (userId: string) => adminService.deleteUser(userId),
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.ORG$DELETE_USER_SUCCESS));
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (error) => {
      const errorMessage = retrieveAxiosErrorMessage(error);
      displayErrorToast(errorMessage || t(I18nKey.ORG$DELETE_USER_ERROR));
    },
  });
};
