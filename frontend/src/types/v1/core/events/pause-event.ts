import { BaseEvent } from "../base/event";

export interface PauseEvent extends BaseEvent {
  /**
   * The source is always "user" for pause events
   */
  source: "user";
}
