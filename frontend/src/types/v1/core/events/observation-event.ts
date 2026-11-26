import { EventID, ToolCallID } from "../base/common";
import { BaseEvent } from "../base/event";
import { Observation } from "../base/observation";

export interface ObservationBaseEvent extends BaseEvent {
  /**
   * The source is always "environment" for observation events
   */
  source: "environment";

  /**
   * The tool name that this observation is responding to
   */
  tool_name: string;

  /**
   * The tool call id that this observation is responding to
   */
  tool_call_id: ToolCallID;
}

export interface ObservationEvent<
  T extends Observation = Observation,
> extends ObservationBaseEvent {
  /**
   * The observation (tool call) sent to LLM
   */
  observation: T;

  /**
   * The action id that this observation is responding to
   */
  action_id: EventID;
}

export interface UserRejectObservation extends ObservationBaseEvent {
  /**
   * Reason for rejecting the action
   */
  rejection_reason: string;

  /**
   * The action id that this observation is responding to
   */
  action_id: EventID;
}

export interface AgentErrorEvent extends BaseEvent {
  /**
   * The source is always "agent" for agent error events
   */
  source: "agent";

  /**
   * The tool name that this observation is responding to
   */
  tool_name: string;

  /**
   * The tool call id that this observation is responding to
   */
  tool_call_id: ToolCallID;

  /**
   * The error message from the scaffold
   */
  error: string;
}
