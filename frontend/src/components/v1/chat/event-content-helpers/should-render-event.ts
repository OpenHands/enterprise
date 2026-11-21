import { OpenHandsParsedEvent } from "#/types/core";
import { OpenHandsEvent } from "#/types/v1/core";
import {
  isActionEvent,
  isObservationEvent,
  isMessageEvent,
  isAgentErrorEvent,
  isConversationStateUpdateEvent,
  isHookExecutionEvent,
  isACPToolCallEvent,
  isStreamingDeltaEvent,
  isV1Event,
} from "#/types/v1/type-guards";

export const shouldRenderEvent = (event: OpenHandsEvent) => {
  if (isConversationStateUpdateEvent(event)) {
    return false;
  }

  if (isActionEvent(event)) {
    // For V1, action is an object with kind property
    const actionType = event.action.kind;

    if (!actionType) {
      return false;
    }

    if (actionType === "ExecuteBashAction" && event.source === "user") {
      return false;
    }

    // Hide PlanningFileEditorAction - handled separately with PlanPreview component
    if (actionType === "PlanningFileEditorAction") {
      return false;
    }

    return true;
  }

  if (isObservationEvent(event)) {
    return true;
  }

  if (isMessageEvent(event)) {
    return true;
  }

  // Render streaming token deltas (the live, growing assistant bubble).
  // Empty boundary deltas are dropped upstream in handleEventForUI.
  if (isStreamingDeltaEvent(event)) {
    return true;
  }

  if (isAgentErrorEvent(event)) {
    return true;
  }

  if (isHookExecutionEvent(event)) {
    return true;
  }

  // Render ACP sub-agent tool call events only at a terminal status. The ACP
  // server emits several events per ``tool_call_id`` (``handleEventForUI``
  // dedupes them into one card); earlier ones would flash an empty
  // ``Input: {}`` / ``Output: [no output]`` card while ``raw_input`` /
  // ``raw_output`` are still streaming in.
  if (isACPToolCallEvent(event)) {
    return event.status === "completed" || event.status === "failed";
  }

  return false;
};

export const hasUserEvent = (events: OpenHandsEvent[]) =>
  events.some((event) => event.source === "user");

/**
 * Narrow a mixed V0/V1 event list to V1 events that actually render in chat.
 * Single source of truth: callers (e.g. `useFilteredEvents`, slash-command
 * interceptors that anchor to the latest visible event) MUST use this rather
 * than re-implementing `isV1Event` + `shouldRenderEvent` chains, so updates
 * to the rendering rules are picked up everywhere.
 */
export const getRenderedV1Events = (
  events: ReadonlyArray<OpenHandsEvent | OpenHandsParsedEvent>,
): OpenHandsEvent[] => events.filter(isV1Event).filter(shouldRenderEvent);
