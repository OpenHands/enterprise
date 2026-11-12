import { useMutation, useQueryClient } from "@tanstack/react-query";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

export const useStopConversation = () => {
  const { organizationId } = useSelectedOrganizationId();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ conversationId }: { conversationId: string }) =>
      organizationService.stopConversation({
        orgId: organizationId!,
        conversationId,
      }),
    meta: { disableToast: true },
    onError: (error) => {
      displayErrorToast(
        retrieveAxiosErrorMessage(error) || "Failed to stop conversation",
      );
    },
    onSuccess: () => {
      displaySuccessToast("Conversation stopped");
      queryClient.invalidateQueries({
        queryKey: ["organizations", "conversations", organizationId],
      });
      queryClient.invalidateQueries({
        queryKey: ["organizations", "conversation-stats", organizationId],
      });
    },
  });
};
