import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18n from "i18next";
import { createUserMessageEvent, useParamsMock } from "test-utils";
import SharedConversation from "#/routes/shared-conversation";
import {
  sharedConversationService,
  SharedConversation as SharedConversationInfo,
} from "#/api/shared-conversation-service.api";

// Initialize i18n for tests; unregistered keys render as the key itself
i18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  ns: ["translation"],
  defaultNS: "translation",
  resources: {
    en: {
      translation: {},
    },
  },
  interpolation: {
    escapeValue: false,
  },
});

vi.mock("#/api/shared-conversation-service.api");

// Mock the V1 Messages component to simplify testing
vi.mock("#/components/v1/chat", () => ({
  Messages: ({ messages }: { messages: unknown[] }) => (
    <div data-testid="v1-messages">
      {messages.map((_, index) => (
        <div key={index} data-testid="v1-message-item" />
      ))}
    </div>
  ),
}));

const conversation: SharedConversationInfo = {
  id: "shared-conv-1",
  created_by_user_id: null,
  sandbox_id: "sandbox-1",
  selected_repository: null,
  selected_branch: null,
  git_provider: null,
  title: "Shared conversation",
  pr_number: [],
  llm_model: null,
  metrics: null,
  parent_conversation_id: null,
  sub_conversation_ids: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const mockContainerSize = (scrollHeight: number, clientHeight: number) => {
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(
    scrollHeight,
  );
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(
    clientHeight,
  );
};

const renderSharedConversation = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter>
          <SharedConversation />
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>,
  );
};

describe("SharedConversation", () => {
  beforeEach(() => {
    useParamsMock.mockReturnValue({ conversationId: "shared-conv-1" });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("loads and renders every page when the first page does not fill the viewport", async () => {
    mockContainerSize(300, 500);
    vi.mocked(sharedConversationService.getSharedConversation).mockResolvedValue(
      conversation,
    );
    vi.mocked(
      sharedConversationService.getSharedConversationEvents,
    ).mockImplementation(async (_conversationId, _limit, pageId) =>
      pageId
        ? { items: [createUserMessageEvent("evt-2")], next_page_id: null }
        : { items: [createUserMessageEvent("evt-1")], next_page_id: "100" },
    );

    renderSharedConversation();

    await waitFor(() => {
      expect(screen.getAllByTestId("v1-message-item")).toHaveLength(2);
    });
    expect(
      sharedConversationService.getSharedConversationEvents,
    ).toHaveBeenCalledWith("shared-conv-1", 100, "100");
  });

  it("keeps loaded events and resumes via retry when a later page fails", async () => {
    mockContainerSize(300, 500);
    vi.mocked(sharedConversationService.getSharedConversation).mockResolvedValue(
      conversation,
    );
    let failNextPage = true;
    vi.mocked(
      sharedConversationService.getSharedConversationEvents,
    ).mockImplementation(async (_conversationId, _limit, pageId) => {
      if (!pageId) {
        return { items: [createUserMessageEvent("evt-1")], next_page_id: "100" };
      }
      if (failNextPage) {
        failNextPage = false;
        throw new Error("gateway timeout");
      }
      return { items: [createUserMessageEvent("evt-2")], next_page_id: null };
    });

    renderSharedConversation();

    await waitFor(() => {
      expect(screen.getByText(/HISTORY_LOAD_INCOMPLETE/)).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("v1-message-item")).toHaveLength(1);
    expect(screen.queryByText(/NOT_FOUND/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("CONVERSATION$RETRY"));

    await waitFor(() => {
      expect(screen.getAllByTestId("v1-message-item")).toHaveLength(2);
    });
  });

  it("shows the not-found state when the initial events load fails", async () => {
    vi.mocked(sharedConversationService.getSharedConversation).mockResolvedValue(
      conversation,
    );
    vi.mocked(
      sharedConversationService.getSharedConversationEvents,
    ).mockRejectedValue(new Error("gateway timeout"));

    renderSharedConversation();

    await waitFor(() => {
      expect(screen.getByText(/NOT_FOUND/)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("v1-messages")).not.toBeInTheDocument();
  });
});
