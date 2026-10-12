import {
  displayErrorToast,
  displayLoadingToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";

export function renderConversationErroredToast(
  _conversationId: string,
  message: string,
): void {
  displayErrorToast(message);
}

export function renderConversationCreatedToast(): void {
  displaySuccessToast("Runtime started");
}

export function renderConversationFinishedToast(): void {
  displaySuccessToast("Conversation finished");
}

export function renderConversationStartingToast(conversationId: string): void {
  displayLoadingToast("Starting runtime...", {
    id: `starting-${conversationId}`,
  });
}
