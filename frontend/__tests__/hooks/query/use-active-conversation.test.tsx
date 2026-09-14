import { conversationFixture } from "../../helpers/native-fixtures";
import React from "react";
import { act, renderHook } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useActiveConversation } from "#/hooks/query/use-active-conversation";
import { useConversationId } from "#/hooks/use-conversation-id";
import V1ConversationService from "#/api/conversation-service/v1-conversation-service.api";
import ConversationService from "#/api/conversation-service/conversation-service.api";
import { V1AppConversation } from "#/api/conversation-service/v1-conversation-service.types";
import { V1SandboxStatus } from "#/api/sandbox-service/sandbox-service.types";

vi.mock("#/hooks/use-conversation-id");
vi.mock("#/api/conversation-service/v1-conversation-service.api", () => ({
  default: { batchGetAppConversations: vi.fn() },
}));
vi.mock("#/api/conversation-service/conversation-service.api", () => ({
  default: { setCurrentConversation: vi.fn() },
}));

const conversation = (status: V1SandboxStatus): V1AppConversation =>
  conversationFixture({
    id: "conv-123",
    sandbox_status: status,
    conversation_url:
      status === "RUNNING"
        ? "https://sandbox.example/conversations/conv-123"
        : null,
    session_api_key: status === "RUNNING" ? "fresh-session-key" : null,
  });

describe("active conversation recovery polling", () => {
  let client: QueryClient;
  const fetchConversations = vi.mocked(
    V1ConversationService.batchGetAppConversations,
  );

  const wrapper = ({ children }: { children: React.ReactNode }): React.JSX.Element => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const advance = async (milliseconds: number): Promise<void> => {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(milliseconds);
    });
  };

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    focusManager.setFocused(true);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(useConversationId).mockReturnValue({
      conversationId: "conv-123",
    });
    fetchConversations.mockResolvedValue([conversation("UNKNOWN")]);
  });

  afterEach(() => {
    client.clear();
    focusManager.setFocused(undefined);
    vi.useRealTimers();
  });

  it.each(["UNKNOWN", "STARTING"] as const)(
    "limits fast %s polling to two minutes",
    async (status) => {
      fetchConversations.mockResolvedValue([conversation(status)]);
      const { unmount } = renderHook(() => useActiveConversation(), {
        wrapper,
      });
      await advance(1);
      expect(fetchConversations).toHaveBeenCalledTimes(1);
      await advance(3000);
      expect(fetchConversations).toHaveBeenCalledTimes(2);
      await advance(117000);
      const callsAfterTwoMinutes = fetchConversations.mock.calls.length;
      await advance(29998);
      expect(fetchConversations).toHaveBeenCalledTimes(callsAfterTwoMinutes);
      await advance(2);
      expect(fetchConversations).toHaveBeenCalledTimes(
        callsAfterTwoMinutes + 1,
      );
      unmount();
      await advance(30000);
      expect(fetchConversations).toHaveBeenCalledTimes(
        callsAfterTwoMinutes + 1,
      );
    },
  );

  it("refreshes access after UNKNOWN becomes RUNNING and returns to normal polling", async () => {
    const { result } = renderHook(() => useActiveConversation(), { wrapper });
    await advance(1);
    expect(result.current.data?.sandbox_status).toBe("UNKNOWN");
    expect(ConversationService.setCurrentConversation).toHaveBeenLastCalledWith(
      conversation("UNKNOWN"),
    );
    fetchConversations.mockResolvedValue([conversation("RUNNING")]);
    await advance(3000);
    expect(result.current.data?.sandbox_status).toBe("RUNNING");
    expect(ConversationService.setCurrentConversation).toHaveBeenLastCalledWith(
      conversation("RUNNING"),
    );
    expect(fetchConversations).toHaveBeenCalledTimes(2);
    await advance(3000);
    expect(fetchConversations).toHaveBeenCalledTimes(2);
    await advance(27000);
    expect(fetchConversations).toHaveBeenCalledTimes(3);
  });

  it("does not poll while the tab is hidden", async () => {
    renderHook(() => useActiveConversation(), { wrapper });
    await advance(1);
    expect(fetchConversations).toHaveBeenCalledTimes(1);
    focusManager.setFocused(false);
    await advance(9000);
    expect(fetchConversations).toHaveBeenCalledTimes(1);
  });

  it("does not poll a start-task ID", async () => {
    vi.mocked(useConversationId).mockReturnValue({
      conversationId: "task-123",
    });
    renderHook(() => useActiveConversation(), { wrapper });
    await advance(30000);
    expect(fetchConversations).not.toHaveBeenCalled();
  });
});
