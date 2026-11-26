/**
 * @deprecated This type is deprecated and will be removed in a future version.
 * Use types in `frontend/src/types/core` instead.
 */
export interface ActionMessage {
  id: number;

  // Either 'agent' or 'user'
  source: "agent" | "user";

  action: string;

  args: Record<string, string>;

  // A friendly message that can be put in the chat log
  message: string;

  timestamp: string;

  llm_metrics?: {
    accumulated_cost: number;
    max_budget_per_task: number | null;
    accumulated_token_usage: {
      prompt_tokens: number;
      completion_tokens: number;
      cache_read_tokens: number;
      cache_write_tokens: number;
      context_window: number;
      per_turn_token: number;
    };
  };

  tool_call_metadata?: {
    model_response?: {
      usage: {
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
      };
    };
  };
}

export interface ObservationMessage {
  observation: string;

  id: number;
  cause: number;

  content: string;

  extras: {
    metadata: Record<string, unknown>;
    error_id: string;
    [key: string]: string | Record<string, unknown>;
  };

  // A friendly message that can be put in the chat log
  message: string;

  timestamp: string;
}

export interface StatusMessage {
  status_update: true;
  type: string;
  id?: string;
  message: string;
  conversation_title?: string;
}
