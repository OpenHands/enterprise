import { useMutation } from "@tanstack/react-query";
import { useActiveConversation } from "#/hooks/query/use-active-conversation";
import { useV1UploadFiles } from "./use-v1-upload-files";
import { FileUploadSuccessResponse } from "#/api/open-hands.types";

interface UnifiedUploadFilesVariables {
  conversationId: string;
  files: File[];
}

/**
 * Unified hook that automatically selects the correct file upload method
 * based on the conversation version (V0 or V1).
 *
 * For V0 conversations: Uses the legacy multi-file upload endpoint
 * For V1 conversations: Uses parallel single-file uploads
 *
 * @returns Mutation hook with the same interface as useUploadFiles
 */
export const useUnifiedUploadFiles = () => {
  const { data: conversation } = useActiveConversation();

  // Initialize both hooks
  const v1Upload = useV1UploadFiles();

  // Create a unified mutation that delegates to the appropriate hook
  return useMutation({
    mutationKey: ["unified-upload-files"],
    mutationFn: async (
      variables: UnifiedUploadFilesVariables,
    ): Promise<FileUploadSuccessResponse> => {
      const { files } = variables;

      // Check if conversation URL is available - required for V1 file uploads
      // This prevents 405 errors when trying to upload before the runtime is ready
      if (!conversation?.conversation_url) {
        throw new Error(
          "Conversation is not ready for file uploads. Please wait for the conversation to fully start.",
        );
      }

      // V1: Use conversation URL and session API key
      return v1Upload.mutateAsync({
        conversationUrl: conversation.conversation_url,
        sessionApiKey: conversation.session_api_key,
        files,
      });
    },
    meta: {
      disableToast: true,
    },
  });
};
