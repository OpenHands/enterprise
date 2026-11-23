import React, {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
} from "react";
import { io, Socket } from "socket.io-client";
import { OpenHandsParsedEvent } from "#/types/core";
import {
  isOpenHandsEvent,
  isAgentStateChangeObservation,
  isStatusUpdate,
} from "#/types/core/guards";
import { AgentState } from "#/types/agent-state";
import {
  renderConversationErroredToast,
  renderConversationCreatedToast,
  renderConversationFinishedToast,
} from "#/components/shared/conversation-toast";
import { Provider } from "#/types/settings";

interface ConversationSocket {
  socket: Socket;
  isConnected: boolean;
  events: OpenHandsParsedEvent[];
}

interface ConversationSubscriptionsContextType {
  activeConversationIds: string[];
  subscribeToConversation: (options: {
    conversationId: string;
    sessionApiKey: string | null;
    providersSet: Provider[];
    baseUrl: string;
    socketPath?: string;
    onEvent?: (event: unknown, conversationId: string) => void;
  }) => void;
  unsubscribeFromConversation: (conversationId: string) => void;
  isSubscribedToConversation: (conversationId: string) => boolean;
  getEventsForConversation: (conversationId: string) => OpenHandsParsedEvent[];
}

const ConversationSubscriptionsContext =
  createContext<ConversationSubscriptionsContextType>({
    activeConversationIds: [],
    subscribeToConversation: () => {
      throw new Error("ConversationSubscriptionsProvider not initialized");
    },
    unsubscribeFromConversation: () => {
      throw new Error("ConversationSubscriptionsProvider not initialized");
    },
    isSubscribedToConversation: () => false,
    getEventsForConversation: () => [],
  });

const isErrorEvent = (
  event: unknown,
): event is { error: true; message: string } =>
  typeof event === "object" &&
  event !== null &&
  "error" in event &&
  event.error === true &&
  "message" in event &&
  typeof event.message === "string";

const isAgentStatusError = (event: unknown): event is OpenHandsParsedEvent =>
  isOpenHandsEvent(event) &&
  isAgentStateChangeObservation(event) &&
  event.extras.agent_state === AgentState.ERROR;

export function ConversationSubscriptionsProvider({
  children,
}: React.PropsWithChildren) {
  const [activeConversationIds, setActiveConversationIds] = useState<string[]>(
    [],
  );
  const [conversationSockets, setConversationSockets] = useState<
    Record<string, ConversationSocket>
  >({});
  const eventHandlersRef = useRef<Record<string, (event: unknown) => void>>({});

  useEffect(
    () => () => {
      const socketsToDisconnect = { ...conversationSockets };

      Object.values(socketsToDisconnect).forEach((socketData) => {
        if (socketData.socket) {
          socketData.socket.removeAllListeners();
          socketData.socket.disconnect();
        }
      });
    },
    [],
  );

  const unsubscribeFromConversation = useCallback((conversationId: string) => {
    setConversationSockets((prev) => {
      const socketData = prev[conversationId];

      if (socketData) {
        const { socket } = socketData;
        const handler = eventHandlersRef.current[conversationId];

        if (socket) {
          if (handler) {
            socket.off("oh_event", handler);
          }
          socket.removeAllListeners();
          socket.disconnect();
        }

        delete eventHandlersRef.current[conversationId];

        const newSockets = { ...prev };
        delete newSockets[conversationId];
        return newSockets;
      }

      return prev; // No change if socket not found
    });

    setActiveConversationIds((prev) =>
      prev.filter((id) => id !== conversationId),
    );
  }, []);

  const subscribeToConversation = useCallback(
    (options: {
      conversationId: string;
      sessionApiKey: string | null;
      providersSet: Provider[];
      baseUrl: string;
      socketPath?: string;
      onEvent?: (event: unknown, conversationId: string) => void;
    }) => {
      const {
        conversationId,
        sessionApiKey,
        providersSet,
        baseUrl,
        socketPath,
        onEvent,
      } = options;

      if (conversationSockets[conversationId]) {
        return;
      }

      const handleOhEvent = (event: unknown) => {
        if (onEvent) {
          onEvent(event, conversationId);
        }

        if (isOpenHandsEvent(event)) {
          setConversationSockets((prev) => {
            if (!prev[conversationId]) return prev;

            return {
              ...prev,
              [conversationId]: {
                ...prev[conversationId],
                events: [...(prev[conversationId]?.events || []), event],
              },
            };
          });
        }

        if (isErrorEvent(event) || isAgentStatusError(event)) {
          renderConversationErroredToast(
            conversationId,
            isErrorEvent(event) ? event.message : "MICROAGENT$UNKNOWN_ERROR",
          );
        } else if (isStatusUpdate(event)) {
          if (event.type === "info" && event.id === "STATUS$STARTING_RUNTIME") {
            renderConversationCreatedToast();
          }
        } else if (
          isOpenHandsEvent(event) &&
          isAgentStateChangeObservation(event)
        ) {
          if (event.extras.agent_state === AgentState.FINISHED) {
            renderConversationFinishedToast();
            unsubscribeFromConversation(conversationId);
          }
        }
      };

      eventHandlersRef.current[conversationId] = handleOhEvent;

      try {
        const socket = io(baseUrl, {
          transports: ["websocket"],
          path: socketPath ?? "/socket.io",
          query: {
            conversation_id: conversationId,
            session_api_key: sessionApiKey,
            providers_set: providersSet,
          },
          reconnection: true,
          reconnectionAttempts: 5,
          reconnectionDelay: 1000,
        });

        socket.on("connect", () => {
          setConversationSockets((prev) => {
            if (!prev[conversationId]) return prev;

            return {
              ...prev,
              [conversationId]: {
                ...prev[conversationId],
                isConnected: true,
              },
            };
          });
        });

        socket.on("connect_error", (error) => {
          // eslint-disable-next-line no-console
          console.warn(
            `Socket for conversation ${conversationId} CONNECTION ERROR:`,
            error,
          );
        });

        socket.on("disconnect", (reason) => {
          // eslint-disable-next-line no-console
          console.warn(
            `Socket for conversation ${conversationId} DISCONNECTED! Reason:`,
            reason,
          );
          setConversationSockets((prev) => {
            if (!prev[conversationId]) return prev;

            return {
              ...prev,
              [conversationId]: {
                ...prev[conversationId],
                isConnected: false,
              },
            };
          });
        });

        socket.on("oh_event", handleOhEvent);

        setConversationSockets((prev) => ({
          ...prev,
          [conversationId]: {
            socket,
            isConnected: socket.connected,
            events: [],
          },
        }));

        setActiveConversationIds((prev) =>
          prev.includes(conversationId) ? prev : [...prev, conversationId],
        );
      } catch {
        delete eventHandlersRef.current[conversationId];
      }
    },
    [conversationSockets],
  );

  const isSubscribedToConversation = useCallback(
    (conversationId: string) => !!conversationSockets[conversationId],
    [conversationSockets],
  );

  const getEventsForConversation = useCallback(
    (conversationId: string) =>
      conversationSockets[conversationId]?.events || [],
    [conversationSockets],
  );

  const value = React.useMemo(
    () => ({
      activeConversationIds,
      subscribeToConversation,
      unsubscribeFromConversation,
      isSubscribedToConversation,
      getEventsForConversation,
    }),
    [
      activeConversationIds,
      subscribeToConversation,
      unsubscribeFromConversation,
      isSubscribedToConversation,
      getEventsForConversation,
    ],
  );

  return (
    <ConversationSubscriptionsContext.Provider value={value}>
      {children}
    </ConversationSubscriptionsContext.Provider>
  );
}

export function useConversationSubscriptions() {
  return useContext(ConversationSubscriptionsContext);
}
