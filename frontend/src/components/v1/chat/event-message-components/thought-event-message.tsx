import React from "react";
import { ActionEvent } from "#/types/v1/core";
import { ChatMessage } from "../../../features/chat/chat-message";

interface ThoughtEventMessageProps {
  event: ActionEvent;
  actions?: Array<{
    icon: React.ReactNode;
    onClick: () => void;
    tooltip?: string;
  }>;
  isFromPlanningAgent?: boolean;
}

export function ThoughtEventMessage({
  event,
  actions,
  isFromPlanningAgent = false,
}: ThoughtEventMessageProps) {
  const thoughtContent = event.thought
    .filter((t) => t.type === "text")
    .map((t) => t.text)
    .join("\n");

  if (!thoughtContent) {
    return null;
  }

  return (
    <ChatMessage
      type="agent"
      message={thoughtContent}
      actions={actions}
      isFromPlanningAgent={isFromPlanningAgent}
    />
  );
}
