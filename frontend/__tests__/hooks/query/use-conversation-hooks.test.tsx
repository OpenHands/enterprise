import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import V1ConversationService from "#/api/conversation-service/v1-conversation-service.api";
import { useConversationHooks } from "#/hooks/query/use-conversation-hooks";
import { AgentState } from "#/types/agent-state";

vi.mock("#/hooks/use-conversation-id", () => ({
  useConversationId: () => ({ conversationId: "conversation-1" }),
}));

vi.mock("#/hooks/use-agent-state", () => ({
  useAgentState: () => ({ curAgentState: AgentState.RUNNING }),
}));

describe("useConversationHooks", () => {
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

  it("fetches hooks without a settings gate", async () => {
    const hooks = [{ event_type: "stop", matchers: [] }];
    const getHooks = vi
      .spyOn(V1ConversationService, "getHooks")
      .mockResolvedValue({ hooks });

    const { result } = renderHook(() => useConversationHooks(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(getHooks).toHaveBeenCalledWith("conversation-1");
    expect(result.current.data).toEqual(hooks);
  });
});
