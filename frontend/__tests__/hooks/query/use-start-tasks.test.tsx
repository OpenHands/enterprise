import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import V1ConversationService from "#/api/conversation-service/v1-conversation-service.api";
import type {
  V1AppConversationStartTask,
  V1AppConversationStartTaskStatus,
} from "#/api/conversation-service/v1-conversation-service.types";
import { useStartTasks } from "#/hooks/query/use-start-tasks";

const createTask = (
  id: string,
  status: V1AppConversationStartTaskStatus,
): V1AppConversationStartTask => ({
  id,
  created_by_user_id: null,
  status,
  detail: null,
  app_conversation_id: null,
  sandbox_id: null,
  agent_server_url: null,
  request: {} as V1AppConversationStartTask["request"],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

describe("useStartTasks", () => {
  let queryClient: QueryClient;

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    vi.restoreAllMocks();
  });

  it("fetches and filters start tasks without a settings gate", async () => {
    const searchStartTasks = vi
      .spyOn(V1ConversationService, "searchStartTasks")
      .mockResolvedValue([
        createTask("working", "WORKING"),
        createTask("ready", "READY"),
        createTask("error", "ERROR"),
      ]);

    const { result } = renderHook(() => useStartTasks(25), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(searchStartTasks).toHaveBeenCalledWith(25);
    expect(result.current.data?.map(({ id }) => id)).toEqual(["working"]);
  });
});
