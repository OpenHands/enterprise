import type { UseQueryResult, DefaultError } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import type { V1AppConversation } from "#/api/conversation-service/v1-conversation-service.types";
import { useConversationId } from "#/hooks/use-conversation-id";
import { useUserConversation } from "./use-user-conversation";
import ConversationService from "#/api/conversation-service/conversation-service.api";

export const useActiveConversation = (): UseQueryResult<
  V1AppConversation | null,
  DefaultError
> => {
  const { conversationId } = useConversationId();
  const recoveryPollingRef = useRef<{
    conversationId: string;
    startedAt: number;
  } | null>(null);

  // Don't poll if this is a task ID (format: "task-{uuid}")
  // Task polling is handled by useTaskPolling hook
  const isTaskId = conversationId.startsWith("task-");
  const actualConversationId = isTaskId ? null : conversationId;

  const userConversation = useUserConversation(
    actualConversationId,
    (query) => {
      const status = query.state.data?.sandbox_status;
      if (status === "STARTING" || status === "UNKNOWN") {
        if (recoveryPollingRef.current?.conversationId !== conversationId) {
          recoveryPollingRef.current = {
            conversationId,
            startedAt: Date.now(),
          };
        }
        // Limit fast recovery polling to two minutes for the active conversation.
        // TanStack Query suspends interval polling while the tab is hidden.
        if (Date.now() - recoveryPollingRef.current.startedAt < 120000) {
          return 3000;
        }
      } else {
        recoveryPollingRef.current = null;
      }
      // TODO: Return conversation title as a WS event to avoid polling
      // This was changed from 5 minutes to 30 seconds to poll for updated conversation title after an auto update
      return 30000; // 30 seconds
    },
  );

  useEffect(() => {
    const conversation = userConversation.data;
    ConversationService.setCurrentConversation(conversation || null);
  }, [
    conversationId,
    userConversation.isFetched,
    userConversation?.data?.sandbox_status,
  ]);
  return userConversation;
};
