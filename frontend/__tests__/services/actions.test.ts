import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { handleStatusMessage } from "#/services/actions";
import { StatusMessage } from "#/types/message";
import { queryClient } from "#/query-client-config";
import { useStatusStore } from "#/stores/status-store";
import { trackError } from "#/utils/error-handler";

vi.mock("#/query-client-config", () => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
}));

vi.mock("#/stores/status-store", () => ({
  useStatusStore: {
    getState: vi.fn(() => ({
      setCurStatusMessage: vi.fn(),
    })),
  },
}));

vi.mock("#/utils/error-handler", () => ({
  trackError: vi.fn(),
}));

describe("handleStatusMessage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  it("should invalidate queries when receiving a conversation title update", () => {
    const statusMessage: StatusMessage = {
      status_update: true,
      type: "info",
      message: "conversation-123",
      conversation_title: "New Conversation Title",
    };

    handleStatusMessage(statusMessage);

    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["user", "conversation", "conversation-123"],
    });
  });

  it("should call setCurStatusMessage for info messages without conversation_title", () => {
    const statusMessage: StatusMessage = {
      status_update: true,
      type: "info",
      message: "Some info message",
    };

    const mockSetCurStatusMessage = vi.fn();
    vi.mocked(useStatusStore.getState).mockReturnValue({
      setCurStatusMessage: mockSetCurStatusMessage,
      curStatusMessage: {
        status_update: true,
        type: "info",
        id: "",
        message: "",
      },
    });

    handleStatusMessage(statusMessage);

    expect(mockSetCurStatusMessage).toHaveBeenCalledWith(statusMessage);

    expect(queryClient.invalidateQueries).not.toHaveBeenCalled();
  });

  it("should call trackError for error messages", () => {
    const statusMessage: StatusMessage = {
      status_update: true,
      type: "error",
      id: "ERROR_ID",
      message: "Some error message",
    };

    handleStatusMessage(statusMessage);

    expect(trackError).toHaveBeenCalledWith({
      message: "Some error message",
      source: "chat",
      metadata: { msgId: "ERROR_ID" },
    });

    expect(queryClient.invalidateQueries).not.toHaveBeenCalled();
  });
});
