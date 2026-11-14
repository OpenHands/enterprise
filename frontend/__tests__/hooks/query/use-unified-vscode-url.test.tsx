import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useUnifiedVSCodeUrl } from "#/hooks/query/use-unified-vscode-url";
import { useBatchAppConversations } from "#/hooks/query/use-batch-app-conversations";
import { useBatchSandboxes } from "#/hooks/query/use-batch-sandboxes";
import { I18nKey } from "#/i18n/declaration";

vi.mock("#/hooks/use-conversation-id", () => ({
  useConversationId: () => ({ conversationId: "conv-1" }),
}));
vi.mock("#/hooks/use-runtime-is-ready", () => ({
  useRuntimeIsReady: () => true,
}));
vi.mock("#/hooks/query/use-batch-app-conversations");
vi.mock("#/hooks/query/use-batch-sandboxes");

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }
  return Wrapper;
};

function mockSandboxVSCodeUrl(url: string) {
  vi.mocked(useBatchAppConversations).mockReturnValue({
    data: [{ sandbox_id: "sb-1" }],
    isLoading: false,
  } as unknown as ReturnType<typeof useBatchAppConversations>);
  vi.mocked(useBatchSandboxes).mockReturnValue({
    data: [{ id: "sb-1", exposed_urls: [{ name: "VSCODE", url }] }],
    isLoading: false,
  } as unknown as ReturnType<typeof useBatchSandboxes>);
}

describe("useUnifiedVSCodeUrl", () => {
  afterEach(() => {
    vi.resetAllMocks();
  });

  it("returns an http(s) sandbox VS Code URL", async () => {
    mockSandboxVSCodeUrl("https://vscode.example.dev/?tkn=abc");

    const { result } = renderHook(() => useUnifiedVSCodeUrl(), {
      wrapper: createWrapper(),
    });

    await waitFor(() =>
      expect(result.current.data).toEqual({
        url: "https://vscode.example.dev/?tkn=abc",
        error: null,
      }),
    );
  });

  it.each([
    // eslint-disable-next-line no-script-url
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "not-a-valid-url",
  ])("reports %s as not available", async (url) => {
    mockSandboxVSCodeUrl(url);

    const { result } = renderHook(() => useUnifiedVSCodeUrl(), {
      wrapper: createWrapper(),
    });

    await waitFor(() =>
      expect(result.current.data).toEqual({
        url: null,
        error: I18nKey.VSCODE$URL_NOT_AVAILABLE,
      }),
    );
  });
});
