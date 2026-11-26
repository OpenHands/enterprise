import { EventID } from "../base/common";
import { BaseEvent } from "../base/event";

export interface CondensationEvent extends BaseEvent {
  /**
   * The source is always "environment" for condensation events
   */
  source: "environment";

  /**
   * The IDs of the events that are being forgotten (removed from the View given to the LLM)
   */
  forgotten_event_ids: EventID[];

  /**
   * An optional summary of the events being forgotten
   */
  summary?: string;

  /**
   * An optional offset to the start of the resulting view indicating where the summary should be inserted
   */
  summary_offset?: number;
}

export interface CondensationRequestEvent extends BaseEvent {
  /**
   * The source is always "environment" for condensation request events
   */
  source: "environment";
}

export interface CondensationSummaryEvent extends BaseEvent {
  /**
   * The source is always "environment" for condensation summary events
   */
  source: "environment";

  /**
   * The summary text
   */
  summary: string;
}
