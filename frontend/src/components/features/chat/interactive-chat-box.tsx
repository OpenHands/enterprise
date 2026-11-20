import { isFileImage } from "#/utils/is-file-image";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { validateFiles } from "#/utils/file-validation";
import { CustomChatInput } from "./custom-chat-input";
import { useBtwInterceptor } from "#/hooks/chat/use-btw-interceptor";
import { useModelInterceptor } from "#/hooks/chat/use-model-interceptor";
import { AgentState } from "#/types/agent-state";
import { useActiveConversation } from "#/hooks/query/use-active-conversation";
import { GitControlBar } from "./git-control-bar";
import { useConversationStore } from "#/stores/conversation-store";
import { useAgentState } from "#/hooks/use-agent-state";
import { processFiles, processImages } from "#/utils/file-processing";
import { useSubConversationTaskPolling } from "#/hooks/query/use-sub-conversation-task-polling";
import { isTaskPolling } from "#/utils/utils";

interface InteractiveChatBoxProps {
  onSubmit: (message: string, images: File[], files: File[]) => void;
  disabled?: boolean;
}

export function InteractiveChatBox({
  onSubmit,
  disabled = false,
}: InteractiveChatBoxProps) {
  const {
    images,
    files,
    addImages,
    addFiles,
    clearAllFiles,
    addFileLoading,
    removeFileLoading,
    addImageLoading,
    removeImageLoading,
    subConversationTaskId,
  } = useConversationStore();
  const { curAgentState } = useAgentState();
  const { data: conversation } = useActiveConversation();

  const { taskStatus: subConversationTaskStatus } =
    useSubConversationTaskPolling(
      subConversationTaskId,
      conversation?.id || null,
    );

  const validateAndFilterFiles = (selectedFiles: File[]) => {
    const validation = validateFiles(selectedFiles, [...images, ...files]);

    if (!validation.isValid) {
      displayErrorToast(`Error: ${validation.errorMessage}`);
      return null;
    }

    const validFiles = selectedFiles.filter((f) => !isFileImage(f));
    const validImages = selectedFiles.filter((f) => isFileImage(f));

    return { validFiles, validImages };
  };

  const showLoadingIndicators = (validFiles: File[], validImages: File[]) => {
    validFiles.forEach((file) => addFileLoading(file.name));
    validImages.forEach((image) => addImageLoading(image.name));
  };

  const handleSuccessfulFiles = (fileResults: { successful: File[] }) => {
    if (fileResults.successful.length > 0) {
      addFiles(fileResults.successful);
      fileResults.successful.forEach((file) => removeFileLoading(file.name));
    }
  };

  const handleSuccessfulImages = (imageResults: { successful: File[] }) => {
    if (imageResults.successful.length > 0) {
      addImages(imageResults.successful);
      imageResults.successful.forEach((image) =>
        removeImageLoading(image.name),
      );
    }
  };

  const handleFailedFiles = (
    fileResults: { failed: { file: File; error: Error }[] },
    imageResults: { failed: { file: File; error: Error }[] },
  ) => {
    fileResults.failed.forEach(({ file, error }) => {
      removeFileLoading(file.name);
      displayErrorToast(
        `Failed to process file ${file.name}: ${error.message}`,
      );
    });

    imageResults.failed.forEach(({ file, error }) => {
      removeImageLoading(file.name);
      displayErrorToast(
        `Failed to process image ${file.name}: ${error.message}`,
      );
    });
  };

  const clearLoadingStates = (validFiles: File[], validImages: File[]) => {
    validFiles.forEach((file) => removeFileLoading(file.name));
    validImages.forEach((image) => removeImageLoading(image.name));
  };

  const handleUpload = async (selectedFiles: File[]) => {
    const result = validateAndFilterFiles(selectedFiles);
    if (!result) return;

    const { validFiles, validImages } = result;

    showLoadingIndicators(validFiles, validImages);

    try {
      const [fileResults, imageResults] = await Promise.all([
        processFiles(validFiles),
        processImages(validImages),
      ]);

      handleSuccessfulFiles(fileResults);
      handleSuccessfulImages(imageResults);

      handleFailedFiles(fileResults, imageResults);
    } catch {
      clearLoadingStates(validFiles, validImages);
      displayErrorToast("An unexpected error occurred while processing files");
    }
  };

  const conversationId = conversation?.id ?? null;
  const submitWithFiles = (message: string) => {
    onSubmit(message, images, files);
    clearAllFiles();
  };
  const handleAfterModel = useBtwInterceptor(conversationId, submitWithFiles);
  const handleSubmit = useModelInterceptor(conversationId, handleAfterModel);

  const handleSuggestionsClick = (suggestion: string) => {
    handleSubmit(suggestion);
  };

  // Allow users to submit messages during LOADING state - they will be
  // queued server-side and delivered when the conversation becomes ready
  const isDisabled =
    disabled ||
    curAgentState === AgentState.AWAITING_USER_CONFIRMATION ||
    isTaskPolling(subConversationTaskStatus);

  return (
    <div data-testid="interactive-chat-box">
      <CustomChatInput
        disabled={isDisabled}
        isNewConversationPending={disabled}
        onSubmit={handleSubmit}
        onFilesPaste={handleUpload}
        sandboxStatus={conversation?.sandbox_status || null}
      />
      <div className="mt-4">
        <GitControlBar onSuggestionsClick={handleSuggestionsClick} />
      </div>
    </div>
  );
}
