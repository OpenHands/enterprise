import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useSandboxRestartWarning } from "#/hooks/use-sandbox-restart-warning";
import { V1AppConversation } from "#/api/conversation-service/v1-conversation-service.types";
import { V1_WebSocketConnectionState } from "#/contexts/conversation-websocket-context";

const mocks = vi.hoisted(() => ({
  conversation: undefined as Partial<V1AppConversation> | undefined,
  connectionState: "OPEN" as V1_WebSocketConnectionState,
  refetch: vi.fn(),
  displayErrorToast: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { reason?: string }) =>
      options?.reason ? `${key}:${options.reason}` : key,
  }),
}));

vi.mock("#/hooks/query/use-active-conversation", () => ({
  useActiveConversation: () => ({
    data: mocks.conversation,
    refetch: mocks.refetch,
  }),
}));

vi.mock("#/hooks/use-unified-websocket-status", () => ({
  useUnifiedWebSocketStatus: () => mocks.connectionState,
}));

vi.mock("#/utils/custom-toast-handlers", () => ({
  displayErrorToast: mocks.displayErrorToast,
}));

const conversation = (
  restartCount: number | null | undefined,
  restartReasons: string[] = [],
  id = "conversation-1",
): Partial<V1AppConversation> => ({
  id,
  sandbox_restart_count: restartCount,
  sandbox_restart_reasons: restartReasons,
});

describe("useSandboxRestartWarning", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.conversation = undefined;
    mocks.connectionState = "OPEN";
  });

  describe("restart detection", () => {
    it("does not warn about restarts that happened before the first read", () => {
      mocks.conversation = conversation(3, ["OOMKilled"]);

      renderHook(() => useSandboxRestartWarning());

      expect(mocks.displayErrorToast).not.toHaveBeenCalled();
    });

    it("warns with the reason when the restart count rises", () => {
      mocks.conversation = conversation(0);
      const { rerender } = renderHook(() => useSandboxRestartWarning());

      mocks.conversation = conversation(1, ["OOMKilled"]);
      rerender();

      expect(mocks.displayErrorToast).toHaveBeenCalledTimes(1);
      expect(mocks.displayErrorToast).toHaveBeenCalledWith(
        "CONVERSATION$SANDBOX_RESTARTED:OOMKilled",
      );
    });

    it("warns once per restart, not once per poll", () => {
      mocks.conversation = conversation(0);
      const { rerender } = renderHook(() => useSandboxRestartWarning());

      mocks.conversation = conversation(1, ["Error"]);
      rerender();
      mocks.conversation = conversation(1, ["Error"]);
      rerender();

      expect(mocks.displayErrorToast).toHaveBeenCalledTimes(1);

      mocks.conversation = conversation(2, ["OOMKilled"]);
      rerender();

      expect(mocks.displayErrorToast).toHaveBeenCalledTimes(2);
    });

    it("falls back to an unknown reason when none is reported", () => {
      mocks.conversation = conversation(0);
      const { rerender } = renderHook(() => useSandboxRestartWarning());

      mocks.conversation = conversation(1, []);
      rerender();

      expect(mocks.displayErrorToast).toHaveBeenCalledWith(
        "CONVERSATION$SANDBOX_RESTARTED:Unknown",
      );
    });

    it("starts a new baseline when the count drops because the pod was replaced", () => {
      mocks.conversation = conversation(3);
      const { rerender } = renderHook(() => useSandboxRestartWarning());

      mocks.conversation = conversation(0);
      rerender();
      expect(mocks.displayErrorToast).not.toHaveBeenCalled();

      mocks.conversation = conversation(1, ["OOMKilled"]);
      rerender();
      expect(mocks.displayErrorToast).toHaveBeenCalledTimes(1);
    });

    it("keeps the baseline across an unknown count", () => {
      mocks.conversation = conversation(2);
      const { rerender } = renderHook(() => useSandboxRestartWarning());

      mocks.conversation = conversation(null);
      rerender();
      mocks.conversation = conversation(undefined);
      rerender();
      expect(mocks.displayErrorToast).not.toHaveBeenCalled();

      mocks.conversation = conversation(3, ["OOMKilled"]);
      rerender();
      expect(mocks.displayErrorToast).toHaveBeenCalledTimes(1);
    });

    it("does not compare counts across different conversations", () => {
      mocks.conversation = conversation(0, [], "conversation-1");
      const { rerender } = renderHook(() => useSandboxRestartWarning());

      mocks.conversation = conversation(4, ["OOMKilled"], "conversation-2");
      rerender();

      expect(mocks.displayErrorToast).not.toHaveBeenCalled();
    });
  });

  describe("WebSocket drops", () => {
    it("refetches the conversation when an open connection closes", () => {
      const { rerender } = renderHook(() => useSandboxRestartWarning());
      expect(mocks.refetch).not.toHaveBeenCalled();

      mocks.connectionState = "CLOSED";
      rerender();

      expect(mocks.refetch).toHaveBeenCalledTimes(1);
    });

    it("does not refetch when a connection that never opened is closed", () => {
      mocks.connectionState = "CONNECTING";
      const { rerender } = renderHook(() => useSandboxRestartWarning());

      mocks.connectionState = "CLOSED";
      rerender();

      expect(mocks.refetch).not.toHaveBeenCalled();
    });
  });
});
