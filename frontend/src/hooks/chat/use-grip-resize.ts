import { useRef, useState, useCallback } from "react";
import { useAutoResize } from "#/hooks/use-auto-resize";
import { CHAT_INPUT } from "#/utils/constants";
import {
  IMessageToSend,
  useConversationStore,
} from "#/stores/conversation-store";

/**
 * Hook for managing grip resize functionality
 */
export const useGripResize = (
  chatInputRef: React.RefObject<HTMLDivElement | null>,
  messageToSend: IMessageToSend | null,
) => {
  const [isGripVisible, setIsGripVisible] = useState(false);

  const { setShouldHideSuggestions } = useConversationStore();

  const gripRef = useRef<HTMLDivElement | null>(null);

  const handleDragStart = useCallback(() => {
    if (gripRef.current) {
      gripRef.current.classList.add("opacity-100");
      gripRef.current.classList.remove("opacity-0");
    }
  }, []);

  const handleDragEnd = useCallback(() => {
    // Restore hover-based visibility
    if (gripRef.current) {
      gripRef.current.classList.remove("opacity-100");
      gripRef.current.classList.add("opacity-0");
    }
  }, []);

  const handleTopEdgeClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setIsGripVisible((prev) => !prev);
  }, []);

  const handleHeightChange = useCallback(
    (height: number) => {
      const shouldHideChatSuggestions = height > CHAT_INPUT.HEIGHT_THRESHOLD;
      setShouldHideSuggestions(shouldHideChatSuggestions);
    },
    [setShouldHideSuggestions],
  );

  const {
    smartResize,
    handleGripMouseDown,
    handleGripTouchStart,
    increaseHeightForEmptyContent,
    resetManualResize,
  } = useAutoResize(chatInputRef as React.RefObject<HTMLElement | null>, {
    minHeight: 20,
    maxHeight: 400,
    onHeightChange: handleHeightChange,
    onGripDragStart: handleDragStart,
    onGripDragEnd: handleDragEnd,
    value: messageToSend ?? undefined,
    enableManualResize: true,
  });

  return {
    gripRef,
    isGripVisible,
    handleTopEdgeClick,
    smartResize,
    handleGripMouseDown,
    handleGripTouchStart,
    increaseHeightForEmptyContent,
    resetManualResize,
  };
};
