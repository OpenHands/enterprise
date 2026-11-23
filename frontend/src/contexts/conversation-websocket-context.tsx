import React, {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  useMemo,
  useRef,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useWebSocket, WebSocketHookOptions } from "#/hooks/use-websocket";
import { useEventStore } from "#/stores/use-event-store";
import { useModelStore } from "#/stores/model-store";
import { getRenderedV1Events } from "#/components/v1/chat/event-content-helpers/should-render-event";
import { updateConversationLlmModelInCache } from "#/hooks/mutation/conversation-mutation-utils";
import { useErrorMessageStore } from "#/stores/error-message-store";
import { useOptimisticUserMessageStore } from "#/stores/optimistic-user-message-store";
import { useV1ConversationStateStore } from "#/stores/v1-conversation-state-store";
import { useCommandStore } from "#/stores/command-store";
import { useBrowserStore } from "#/stores/browser-store";
import {
  isV1Event,
  isAgentErrorEvent,
  isUserMessageEvent,
  isActionEvent,
  isConversationStateUpdateEvent,
  isFullStateConversationStateUpdateEvent,
  isAgentStatusConversationStateUpdateEvent,
  isStatsConversationStateUpdateEvent,
  isSwitchLLMObservationEvent,
  isExecuteBashActionEvent,
  isExecuteBashObservationEvent,
  isDisplayableErrorEvent,
  isPlanningFileEditorObservationEvent,
  isBrowserObservationEvent,
  isBrowserNavigateActionEvent,
} from "#/types/v1/type-guards";
import { ConversationStateUpdateEventStats } from "#/types/v1/core/events/conversation-state-event";
import type {
  ConversationErrorEvent,
  ServerErrorEvent,
} from "#/types/v1/core/events/conversation-state-event";
import { handleActionEventCacheInvalidation } from "#/utils/cache-utils";
import { buildWebSocketUrl } from "#/utils/websocket-url";
import type {
  V1AppConversation,
  V1SendMessageRequest,
} from "#/api/conversation-service/v1-conversation-service.types";
import EventService from "#/api/event-service/event-service.api";
import PendingMessageService from "#/api/pending-message-service/pending-message-service.api";
import { useConversationStore } from "#/stores/conversation-store";
import { classifyBudgetOrCreditError, trackError } from "#/utils/error-handler";
import { useReadConversationFile } from "#/hooks/mutation/use-read-conversation-file";
import useMetricsStore from "#/stores/metrics-store";
import { I18nKey } from "#/i18n/declaration";
import { useConversationHistory } from "#/hooks/query/use-conversation-history";
import { setConversationState } from "#/utils/conversation-local-storage";

// eslint-disable-next-line @typescript-eslint/naming-convention
export type V1_WebSocketConnectionState =
  "CONNECTING" | "OPEN" | "CLOSED" | "CLOSING";

interface SendMessageResult {
  queued: boolean; // true if message was queued for later delivery, false if sent immediately
}

interface ConversationWebSocketContextType {
  connectionState: V1_WebSocketConnectionState;
  sendMessage: (message: V1SendMessageRequest) => Promise<SendMessageResult>;
  isLoadingHistory: boolean;
  /** REST history preload failed (fully, or partway through the archived
   * conversation pagination). */
  historyLoadFailed?: boolean;
  retryHistoryLoad?: () => void;
}

const ConversationWebSocketContext = createContext<
  ConversationWebSocketContextType | undefined
>(undefined);

const getBudgetErrorMessageKey = (errorMessage: string) => {
  const errorType = classifyBudgetOrCreditError(errorMessage);
  if (errorType === "credit") {
    return I18nKey.STATUS$ERROR_LLM_OUT_OF_CREDITS;
  }
  if (errorType === "budget") {
    return I18nKey.STATUS$ERROR_BUDGET_LIMIT_REACHED;
  }
  return null;
};

export function ConversationWebSocketProvider({
  children,
  conversationId,
  conversationUrl,
  sessionApiKey,
  subConversations,
  subConversationIds,
}: {
  children: React.ReactNode;
  conversationId?: string;
  conversationUrl?: string | null;
  sessionApiKey?: string | null;
  subConversations?: V1AppConversation[];
  subConversationIds?: string[];
}) {
  // Separate connection state tracking for each WebSocket
  const [mainConnectionState, setMainConnectionState] =
    useState<V1_WebSocketConnectionState>("CONNECTING");
  const [planningConnectionState, setPlanningConnectionState] =
    useState<V1_WebSocketConnectionState>("CONNECTING");

  // Track if we've ever successfully connected for each connection
  // Don't show errors until after first successful connection
  const hasConnectedRefMain = React.useRef(false);
  const hasConnectedRefPlanning = React.useRef(false);

  const queryClient = useQueryClient();
  const { addEvent } = useEventStore();
  const { setErrorMessage, removeErrorMessage } = useErrorMessageStore();
  const { removeOptimisticUserMessage } = useOptimisticUserMessageStore();
  const { setExecutionStatus } = useV1ConversationStateStore();
  const { appendInput, appendOutput } = useCommandStore();

  const [isLoadingHistoryMain, setIsLoadingHistoryMain] = useState(true);
  const [isLoadingHistoryPlanning, setIsLoadingHistoryPlanning] =
    useState(true);
  const [expectedEventCountMain, setExpectedEventCountMain] = useState<
    number | null
  >(null);
  const [expectedEventCountPlanning, setExpectedEventCountPlanning] = useState<
    number | null
  >(null);

  const { setPlanContent } = useConversationStore();

  const { mutate: readConversationFile } = useReadConversationFile();

  const receivedEventCountRefMain = useRef(0);
  const receivedEventCountRefPlanning = useRef(0);

  // Track the latest PlanningFileEditorObservation for Plan.md during history replay
  const latestPlanningFileEventRef = useRef<{
    path: string;
    conversationId: string;
  } | null>(null);

  const isPlanFilePath = (path: string | null): boolean =>
    path?.toUpperCase().endsWith("PLAN.MD") ?? false;

  // Budget/credit errors persist until an agent event proves the LLM is working.
  const handleNonErrorEvent = useCallback(
    (event: { source?: string }) => {
      const currentError = useErrorMessageStore.getState().errorMessage;
      const isBudgetError =
        currentError === I18nKey.STATUS$ERROR_LLM_OUT_OF_CREDITS ||
        currentError === I18nKey.STATUS$ERROR_BUDGET_LIMIT_REACHED;
      const isAgentEvent = event.source === "agent";

      if (isBudgetError && !isAgentEvent) {
        return; // Keep budget error visible
      }

      removeErrorMessage();
    },
    [removeErrorMessage],
  );

  const updateMetricsFromStats = useCallback(
    (event: ConversationStateUpdateEventStats) => {
      if (event.value.usage_to_metrics?.agent) {
        const agentMetrics = event.value.usage_to_metrics.agent;
        const metrics = {
          cost: agentMetrics.accumulated_cost,
          max_budget_per_task: agentMetrics.max_budget_per_task ?? null,
          usage: agentMetrics.accumulated_token_usage
            ? {
                prompt_tokens:
                  agentMetrics.accumulated_token_usage.prompt_tokens,
                completion_tokens:
                  agentMetrics.accumulated_token_usage.completion_tokens,
                cache_read_tokens:
                  agentMetrics.accumulated_token_usage.cache_read_tokens,
                cache_write_tokens:
                  agentMetrics.accumulated_token_usage.cache_write_tokens,
                context_window:
                  agentMetrics.accumulated_token_usage.context_window,
                per_turn_token:
                  agentMetrics.accumulated_token_usage.per_turn_token,
              }
            : null,
        };
        useMetricsStore.getState().setMetrics(metrics);
      }
    },
    [],
  );

  // Only build the URL once conversationId and conversationUrl exist, so no
  // connection is attempted during the task polling phase.
  const wsUrl = useMemo(() => {
    if (!conversationId || !conversationUrl) {
      return null;
    }
    return buildWebSocketUrl(conversationId, conversationUrl);
  }, [conversationId, conversationUrl]);

  const planningAgentWsUrl = useMemo(() => {
    if (!subConversations?.length) {
      return null;
    }

    // Currently, there is only one sub-conversation and it uses the planning agent.
    const planningAgentConversation = subConversations[0];

    if (
      !planningAgentConversation?.id ||
      !planningAgentConversation.conversation_url
    ) {
      return null;
    }

    return buildWebSocketUrl(
      planningAgentConversation.id,
      planningAgentConversation.conversation_url,
    );
  }, [subConversations]);

  const connectionState = useMemo<V1_WebSocketConnectionState>(() => {
    if (!planningAgentWsUrl) {
      return mainConnectionState;
    }

    if (
      mainConnectionState === "CONNECTING" ||
      planningConnectionState === "CONNECTING"
    ) {
      return "CONNECTING";
    }

    if (mainConnectionState === "OPEN" && planningConnectionState === "OPEN") {
      return "OPEN";
    }

    if (
      mainConnectionState === "CLOSED" &&
      planningConnectionState === "CLOSED"
    ) {
      return "CLOSED";
    }

    if (
      mainConnectionState === "CLOSING" ||
      planningConnectionState === "CLOSING"
    ) {
      return "CLOSING";
    }

    return "CLOSED";
  }, [mainConnectionState, planningConnectionState, planningAgentWsUrl]);

  useEffect(() => {
    if (
      expectedEventCountMain !== null &&
      receivedEventCountRefMain.current >= expectedEventCountMain &&
      isLoadingHistoryMain
    ) {
      setIsLoadingHistoryMain(false);
    }
  }, [expectedEventCountMain, isLoadingHistoryMain, receivedEventCountRefMain]);

  useEffect(() => {
    if (
      expectedEventCountPlanning !== null &&
      receivedEventCountRefPlanning.current >= expectedEventCountPlanning &&
      isLoadingHistoryPlanning
    ) {
      setIsLoadingHistoryPlanning(false);
    }
  }, [
    expectedEventCountPlanning,
    isLoadingHistoryPlanning,
    receivedEventCountRefPlanning,
  ]);

  // Call API once after history loading completes if we tracked any PlanningFileEditorObservation events
  useEffect(() => {
    if (!isLoadingHistoryPlanning && latestPlanningFileEventRef.current) {
      const { path, conversationId: currentPlanningConversationId } =
        latestPlanningFileEventRef.current;

      readConversationFile(
        {
          conversationId: currentPlanningConversationId,
          filePath: path,
        },
        {
          onSuccess: (fileContent) => {
            setPlanContent(fileContent);
          },
          onError: (error) => {
            // eslint-disable-next-line no-console
            console.warn("Failed to read conversation file:", error);
          },
        },
      );

      latestPlanningFileEventRef.current = null;
    }
  }, [isLoadingHistoryPlanning, readConversationFile, setPlanContent]);

  useEffect(() => {
    hasConnectedRefMain.current = false;
    setIsLoadingHistoryPlanning(!!subConversationIds?.length);
    setExpectedEventCountPlanning(null);
    receivedEventCountRefPlanning.current = 0;
    latestPlanningFileEventRef.current = null;
  }, [subConversationIds]);

  const isLoadingHistory = useMemo(
    () => isLoadingHistoryMain || isLoadingHistoryPlanning,
    [isLoadingHistoryMain, isLoadingHistoryPlanning],
  );

  useEffect(() => {
    hasConnectedRefPlanning.current = false;
    setIsLoadingHistoryMain(true);
    setExpectedEventCountMain(null);
    receivedEventCountRefMain.current = 0;
    latestPlanningFileEventRef.current = null;
  }, [conversationId]);

  const {
    data: preloadedEvents,
    isFetched: isHistoryFetched,
    isError: isHistoryError,
    isFetchingMore: isFetchingMoreHistory,
    fetchNextPage: fetchNextHistoryPage,
    refetch: refetchHistory,
  } = useConversationHistory(conversationId);

  useEffect(() => {
    // Don't do anything until the history query has completed
    // This prevents prematurely setting loading to false before data is available
    if (!isHistoryFetched) {
      return;
    }

    if (!preloadedEvents || preloadedEvents.length === 0) {
      setIsLoadingHistoryMain(false);
      return;
    }

    // Add all preloaded events to the store (it dedupes by id, so re-running
    // as archived conversations load additional pages is safe)
    for (const event of preloadedEvents) {
      addEvent(event);
    }

    if (!isFetchingMoreHistory) {
      setIsLoadingHistoryMain(false);
    }
  }, [preloadedEvents, isHistoryFetched, isFetchingMoreHistory, addEvent]);

  const retryHistoryLoad = useCallback(() => {
    // A mid-pagination failure only needs the failed page again; a failed
    // initial load needs a full refetch.
    if (preloadedEvents !== undefined) {
      fetchNextHistoryPage();
    } else {
      refetchHistory();
    }
  }, [preloadedEvents, fetchNextHistoryPage, refetchHistory]);

  const handleMainMessage = useCallback(
    (messageEvent: MessageEvent) => {
      try {
        const event = JSON.parse(messageEvent.data);

        // Count while loading even before the expected count is known
        if (isLoadingHistoryMain) {
          receivedEventCountRefMain.current += 1;

          if (
            expectedEventCountMain !== null &&
            receivedEventCountRefMain.current >= expectedEventCountMain
          ) {
            setIsLoadingHistoryMain(false);
          }
        }

        if (isV1Event(event)) {
          addEvent(event);

          // AgentErrorEvent errors are displayed inline in the chat, not as banners
          if (isDisplayableErrorEvent(event)) {
            const errorEvent = event as
              ConversationErrorEvent | ServerErrorEvent;
            trackError({
              message: errorEvent.detail,
              source: "conversation",
              metadata: {
                eventId: errorEvent.id,
                errorCode: errorEvent.code,
              },
            });
            const budgetErrorKey = getBudgetErrorMessageKey(errorEvent.detail);
            if (budgetErrorKey) {
              setErrorMessage(budgetErrorKey);
            } else {
              setErrorMessage(errorEvent.detail);
            }
          } else {
            handleNonErrorEvent(event);
          }

          if (isAgentErrorEvent(event)) {
            trackError({
              message: event.error,
              source: "agent",
              metadata: {
                eventId: event.id,
                toolName: event.tool_name,
                toolCallId: event.tool_call_id,
              },
            });
            const budgetErrorKey = getBudgetErrorMessageKey(event.error);
            if (budgetErrorKey) {
              setErrorMessage(budgetErrorKey);
            } else {
              setErrorMessage(event.error);
            }
          }

          if (isUserMessageEvent(event)) {
            removeOptimisticUserMessage();
            // Clear draft from localStorage - message was successfully delivered
            if (conversationId) {
              setConversationState(conversationId, { draftMessage: null });
            }
          }

          if (isActionEvent(event)) {
            const currentConversationId =
              conversationId || "test-conversation-id"; // TODO: Get from context
            handleActionEventCacheInvalidation(
              event,
              currentConversationId,
              queryClient,
            );
          }

          // TODO: Tests
          if (isConversationStateUpdateEvent(event)) {
            if (isFullStateConversationStateUpdateEvent(event)) {
              setExecutionStatus(event.value.execution_status);
            }
            if (isAgentStatusConversationStateUpdateEvent(event)) {
              setExecutionStatus(event.value);
            }
            if (isStatsConversationStateUpdateEvent(event)) {
              updateMetricsFromStats(event);
            }
          }

          // The agent switched its own LLM (via the built-in switch_llm tool).
          // Mirror the manual-switch UX: record the new profile (so the
          // switch-profile button flips by name, unambiguous even when several
          // profiles share a model string) and patch the running model into the
          // conversation cache so the chat header updates instantly.
          //
          // We deliberately do NOT invalidate the conversation query here. The
          // app-server only learns of the switch via the webhook, which is
          // batched (``event_buffer_size`` / ``flush_delay``), and the
          // conversation endpoint reports the *persisted* ``llm_model`` (not the
          // sandbox's live model). An immediate refetch would therefore read the
          // pre-switch model and clobber the patch above. The persisted value
          // catches up on the next poll once the webhook has flushed.
          if (
            conversationId &&
            isSwitchLLMObservationEvent(event) &&
            !event.observation.is_error
          ) {
            const last = getRenderedV1Events(
              useEventStore.getState().uiEvents,
            ).at(-1);
            const anchorEventId = last ? String(last.id) : null;
            useModelStore
              .getState()
              .recordSwitch(
                conversationId,
                anchorEventId,
                event.observation.profile_name,
              );

            if (event.observation.active_model) {
              updateConversationLlmModelInCache(
                queryClient,
                conversationId,
                event.observation.active_model,
              );
            }
          }

          if (isExecuteBashActionEvent(event)) {
            appendInput(event.action.command);
          }

          if (isExecuteBashObservationEvent(event)) {
            const textContent = event.observation.content
              .filter((c) => c.type === "text")
              .map((c) => c.text)
              .join("\n");
            appendOutput(textContent);
          }

          if (isBrowserObservationEvent(event)) {
            const { screenshot_data: screenshotData } = event.observation;
            if (screenshotData) {
              const screenshotSrc = screenshotData.startsWith("data:")
                ? screenshotData
                : `data:image/png;base64,${screenshotData}`;
              useBrowserStore.getState().setScreenshotSrc(screenshotSrc);
            }
          }

          if (isBrowserNavigateActionEvent(event)) {
            useBrowserStore.getState().setUrl(event.action.url);
          }
        }
      } catch (error) {
        // eslint-disable-next-line no-console
        console.warn("Failed to parse WebSocket message as JSON:", error);
      }
    },
    [
      addEvent,
      isLoadingHistoryMain,
      expectedEventCountMain,
      setErrorMessage,
      removeErrorMessage,
      removeOptimisticUserMessage,
      queryClient,
      conversationId,
      setExecutionStatus,
      appendInput,
      appendOutput,
      updateMetricsFromStats,
    ],
  );

  const handlePlanningMessage = useCallback(
    (messageEvent: MessageEvent) => {
      try {
        const event = JSON.parse(messageEvent.data);

        // Count while loading even before the expected count is known
        if (isLoadingHistoryPlanning) {
          receivedEventCountRefPlanning.current += 1;

          if (
            expectedEventCountPlanning !== null &&
            receivedEventCountRefPlanning.current >= expectedEventCountPlanning
          ) {
            setIsLoadingHistoryPlanning(false);
          }
        }

        if (isV1Event(event)) {
          const eventWithPlanningFlag = {
            ...event,
            isFromPlanningAgent: true,
          };
          addEvent(eventWithPlanningFlag);

          // AgentErrorEvent errors are displayed inline in the chat, not as banners
          if (isDisplayableErrorEvent(event)) {
            const errorEvent = event as
              ConversationErrorEvent | ServerErrorEvent;
            trackError({
              message: errorEvent.detail,
              source: "planning_conversation",
              metadata: {
                eventId: errorEvent.id,
                errorCode: errorEvent.code,
              },
            });
            const budgetErrorKey = getBudgetErrorMessageKey(errorEvent.detail);
            if (budgetErrorKey) {
              setErrorMessage(budgetErrorKey);
            } else {
              setErrorMessage(errorEvent.detail);
            }
          } else {
            handleNonErrorEvent(event);
          }

          if (isAgentErrorEvent(event)) {
            trackError({
              message: event.error,
              source: "planning_agent",
              metadata: {
                eventId: event.id,
                toolName: event.tool_name,
                toolCallId: event.tool_call_id,
              },
            });
            const budgetErrorKey = getBudgetErrorMessageKey(event.error);
            if (budgetErrorKey) {
              setErrorMessage(budgetErrorKey);
            } else {
              setErrorMessage(event.error);
            }
          }

          if (isUserMessageEvent(event)) {
            removeOptimisticUserMessage();
            // Clear draft from localStorage - message was successfully delivered
            // Use main conversationId since user types in main conversation input
            if (conversationId) {
              setConversationState(conversationId, { draftMessage: null });
            }
          }

          if (isActionEvent(event)) {
            const planningAgentConversation = subConversations?.[0];
            const currentConversationId =
              planningAgentConversation?.id || "test-conversation-id"; // TODO: Get from context
            handleActionEventCacheInvalidation(
              event,
              currentConversationId,
              queryClient,
            );
          }

          // TODO: Tests
          if (isConversationStateUpdateEvent(event)) {
            if (isFullStateConversationStateUpdateEvent(event)) {
              setExecutionStatus(event.value.execution_status);
            }
            if (isAgentStatusConversationStateUpdateEvent(event)) {
              setExecutionStatus(event.value);
            }
            if (isStatsConversationStateUpdateEvent(event)) {
              updateMetricsFromStats(event);
            }
          }

          if (isExecuteBashActionEvent(event)) {
            appendInput(event.action.command);
          }

          if (isExecuteBashObservationEvent(event)) {
            const textContent = event.observation.content
              .filter((c) => c.type === "text")
              .map((c) => c.text)
              .join("\n");
            appendOutput(textContent);
          }

          // Handle PlanningFileEditorObservation - only update plan for Plan.md
          if (isPlanningFileEditorObservationEvent(event)) {
            const { path } = event.observation;
            if (isPlanFilePath(path)) {
              const planningAgentConversation = subConversations?.[0];
              const planningConversationId = planningAgentConversation?.id;

              if (planningConversationId && path) {
                if (isLoadingHistoryPlanning) {
                  latestPlanningFileEventRef.current = {
                    path,
                    conversationId: planningConversationId,
                  };
                } else {
                  readConversationFile(
                    {
                      conversationId: planningConversationId,
                      filePath: path,
                    },
                    {
                      onSuccess: (fileContent) => {
                        setPlanContent(fileContent);
                      },
                      onError: (error) => {
                        // eslint-disable-next-line no-console
                        console.warn(
                          "Failed to read conversation file:",
                          error,
                        );
                      },
                    },
                  );
                }
              }
            }
          }
        }
      } catch (error) {
        // eslint-disable-next-line no-console
        console.warn("Failed to parse WebSocket message as JSON:", error);
      }
    },
    [
      addEvent,
      isLoadingHistoryPlanning,
      expectedEventCountPlanning,
      setErrorMessage,
      removeErrorMessage,
      removeOptimisticUserMessage,
      queryClient,
      subConversations,
      conversationId,
      setExecutionStatus,
      appendInput,
      appendOutput,
      readConversationFile,
      setPlanContent,
      updateMetricsFromStats,
    ],
  );

  const mainWebsocketOptions: WebSocketHookOptions = useMemo(() => {
    const queryParams: Record<string, string | boolean> = {
      resend_all: true,
    };

    if (sessionApiKey) {
      queryParams.session_api_key = sessionApiKey;
    }

    return {
      queryParams,
      reconnect: { enabled: true },
      onOpen: async () => {
        setMainConnectionState("OPEN");
        hasConnectedRefMain.current = true; // Mark that we've successfully connected
        removeErrorMessage(); // Clear any previous error messages on successful connection

        if (conversationId && conversationUrl) {
          try {
            const count = await EventService.getEventCount(
              conversationId,
              conversationUrl,
              sessionApiKey,
            );
            setExpectedEventCountMain(count);

            if (count === 0) {
              setIsLoadingHistoryMain(false);
            }
          } catch (error) {
            // Fall back to marking as loaded to avoid infinite loading state
            setIsLoadingHistoryMain(false);
          }
        }
      },
      onClose: () => {
        setMainConnectionState("CLOSED");
        // Recovery is handled by useSandboxRecovery on tab focus/page refresh
        // No error message needed - silent recovery provides better UX
      },
      onError: () => {
        setMainConnectionState("CLOSED");
        if (hasConnectedRefMain.current) {
          const currentError = useErrorMessageStore.getState().errorMessage;
          if (
            currentError !== I18nKey.STATUS$ERROR_LLM_OUT_OF_CREDITS &&
            currentError !== I18nKey.STATUS$ERROR_BUDGET_LIMIT_REACHED
          ) {
            setErrorMessage("Failed to connect to server");
          }
        }
      },
      onMessage: handleMainMessage,
    };
  }, [
    handleMainMessage,
    setErrorMessage,
    removeErrorMessage,
    sessionApiKey,
    conversationId,
    conversationUrl,
  ]);

  const planningWebsocketOptions: WebSocketHookOptions = useMemo(() => {
    const queryParams: Record<string, string | boolean> = {
      resend_all: true,
    };

    if (sessionApiKey) {
      queryParams.session_api_key = sessionApiKey;
    }

    const planningAgentConversation = subConversations?.[0];

    return {
      queryParams,
      reconnect: { enabled: true },
      onOpen: async () => {
        setPlanningConnectionState("OPEN");
        hasConnectedRefPlanning.current = true; // Mark that we've successfully connected
        removeErrorMessage(); // Clear any previous error messages on successful connection

        if (
          planningAgentConversation?.id &&
          planningAgentConversation.conversation_url
        ) {
          try {
            const count = await EventService.getEventCount(
              planningAgentConversation.id,
              planningAgentConversation.conversation_url,
              planningAgentConversation.session_api_key,
            );
            setExpectedEventCountPlanning(count);

            if (count === 0) {
              setIsLoadingHistoryPlanning(false);
            }
          } catch (error) {
            // Fall back to marking as loaded to avoid infinite loading state
            setIsLoadingHistoryPlanning(false);
          }
        }
      },
      onClose: () => {
        setPlanningConnectionState("CLOSED");
        // Recovery is handled by useSandboxRecovery on tab focus/page refresh
        // No error message needed - silent recovery provides better UX
      },
      onError: () => {
        setPlanningConnectionState("CLOSED");
        if (hasConnectedRefPlanning.current) {
          const currentError = useErrorMessageStore.getState().errorMessage;
          if (
            currentError !== I18nKey.STATUS$ERROR_LLM_OUT_OF_CREDITS &&
            currentError !== I18nKey.STATUS$ERROR_BUDGET_LIMIT_REACHED
          ) {
            setErrorMessage("Failed to connect to server");
          }
        }
      },
      onMessage: handlePlanningMessage,
    };
  }, [
    handlePlanningMessage,
    setErrorMessage,
    removeErrorMessage,
    sessionApiKey,
    subConversations,
  ]);

  // Only attempt a connection with a valid URL (none during task polling).
  const websocketUrl = wsUrl;
  const { socket: mainSocket } = useWebSocket(
    websocketUrl || "",
    mainWebsocketOptions,
  );

  const { socket: planningAgentSocket } = useWebSocket(
    planningAgentWsUrl || "",
    planningWebsocketOptions,
  );

  // Falls back to REST API queue when WebSocket is not connected
  const sendMessage = useCallback(
    async (message: V1SendMessageRequest): Promise<SendMessageResult> => {
      const currentMode = useConversationStore.getState().conversationMode;
      const currentSocket =
        currentMode === "plan" ? planningAgentSocket : mainSocket;

      if (currentSocket?.readyState !== WebSocket.OPEN) {
        // Message will be delivered automatically when conversation becomes ready
        if (!conversationId) {
          const error = new Error("No conversation ID available");
          setErrorMessage(error.message);
          throw error;
        }

        try {
          await PendingMessageService.queueMessage(conversationId, {
            role: "user",
            content: message.content,
          });
          // Message queued successfully - it will be delivered when ready
          // Return queued: true so caller knows not to show optimistic UI
          return { queued: true };
        } catch (error) {
          const errorMessage =
            error instanceof Error
              ? error.message
              : "Failed to queue message for delivery";
          setErrorMessage(errorMessage);
          throw error;
        }
      }

      try {
        currentSocket.send(JSON.stringify(message));
        return { queued: false };
      } catch (error) {
        const errorMessage =
          error instanceof Error ? error.message : "Failed to send message";
        setErrorMessage(errorMessage);
        throw error;
      }
    },
    [mainSocket, planningAgentSocket, setErrorMessage, conversationId],
  );

  useEffect(() => {
    if (mainSocket && wsUrl) {
      const updateState = () => {
        switch (mainSocket.readyState) {
          case WebSocket.CONNECTING:
            setMainConnectionState("CONNECTING");
            break;
          case WebSocket.OPEN:
            setMainConnectionState("OPEN");
            break;
          case WebSocket.CLOSING:
            setMainConnectionState("CLOSING");
            break;
          case WebSocket.CLOSED:
            setMainConnectionState("CLOSED");
            break;
          default:
            setMainConnectionState("CLOSED");
            break;
        }
      };

      updateState();
    }
  }, [mainSocket, wsUrl]);

  useEffect(() => {
    if (planningAgentSocket && planningAgentWsUrl) {
      const updateState = () => {
        switch (planningAgentSocket.readyState) {
          case WebSocket.CONNECTING:
            setPlanningConnectionState("CONNECTING");
            break;
          case WebSocket.OPEN:
            setPlanningConnectionState("OPEN");
            break;
          case WebSocket.CLOSING:
            setPlanningConnectionState("CLOSING");
            break;
          case WebSocket.CLOSED:
            setPlanningConnectionState("CLOSED");
            break;
          default:
            setPlanningConnectionState("CLOSED");
            break;
        }
      };

      updateState();
    }
  }, [planningAgentSocket, planningAgentWsUrl]);

  const contextValue = useMemo(
    () => ({
      connectionState,
      sendMessage,
      isLoadingHistory,
      historyLoadFailed: isHistoryError,
      retryHistoryLoad,
    }),
    [
      connectionState,
      sendMessage,
      isLoadingHistory,
      isHistoryError,
      retryHistoryLoad,
    ],
  );

  return (
    <ConversationWebSocketContext.Provider value={contextValue}>
      {children}
    </ConversationWebSocketContext.Provider>
  );
}

export const useConversationWebSocket =
  (): ConversationWebSocketContextType | null => {
    const context = useContext(ConversationWebSocketContext);
    // Return null instead of throwing when not in provider
    // This allows the hook to be called conditionally based on conversation version
    return context || null;
  };
