import { V1AppConversation } from "#/api/conversation-service/v1-conversation-service.types";
import { AxiosHeaders, AxiosResponse } from "axios";
import {
  QueryClient,
  QueryObserver,
  UseQueryResult,
} from "@tanstack/react-query";

export interface Deferred<Value> {
  promise: Promise<Value>;
  resolve: (value: Value) => void;
}
export function deferred<Value>(): Deferred<Value> {
  let complete: ((value: Value) => void) | undefined;
  const promise = new Promise<Value>((resolve) => {
    complete = resolve;
  });
  return {
    promise,
    resolve: (value: Value): void => {
      if (!complete)
        throw new Error("The deferred promise is not initialized.");
      complete(value);
    },
  };
}
export function axiosResponse<Data>(
  data: Data,
  status: number = 200,
): AxiosResponse<Data, unknown> {
  return {
    data,
    status,
    statusText: "",
    headers: {},
    config: { headers: new AxiosHeaders() },
  };
}
export function queryResult<Data>(data: Data): UseQueryResult<Data> {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return new QueryObserver(client, {
    queryKey: ["fixture"],
    initialData: data,
    queryFn: (): Data => data,
  }).getCurrentResult();
}

export function conversationFixture(
  overrides: Partial<V1AppConversation> = {},
): V1AppConversation {
  return {
    id: "conv-123",
    created_by_user_id: null,
    sandbox_id: "sandbox-123",
    selected_repository: null,
    selected_branch: null,
    git_provider: null,
    title: "Synthetic conversation",
    trigger: null,
    pr_number: [],
    llm_model: null,
    metrics: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    sandbox_status: "RUNNING",
    execution_status: null,
    conversation_url: null,
    session_api_key: null,
    sub_conversation_ids: [],
    ...overrides,
  };
}

export function containingForm(element: HTMLElement): HTMLFormElement {
  const form = element.closest("form");
  if (!form) throw new Error("Expected the control to belong to a form.");
  return form;
}
