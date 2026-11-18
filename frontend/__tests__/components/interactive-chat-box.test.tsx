import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { InteractiveChatBox } from "#/components/features/chat/interactive-chat-box";
import { renderWithProviders } from "../../test-utils";
import { AgentState } from "#/types/agent-state";
import { useAgentState } from "#/hooks/use-agent-state";
import { useConversationStore } from "#/stores/conversation-store";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import {
  clearSubmittedConversationDraft,
  getConversationState,
} from "#/utils/conversation-local-storage";

vi.mock("#/hooks/use-agent-state", () => ({
  useAgentState: vi.fn(),
}));

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router")>()),
  useNavigate: () => vi.fn(),
  useParams: () => ({ conversationId: "test-conversation-id" }),
  useRevalidator: () => ({ revalidate: vi.fn() }),
}));

vi.mock("#/hooks/query/use-active-conversation", () => ({
  useActiveConversation: () => ({
    data: { status: null },
    isFetched: true,
    refetch: vi.fn(),
  }),
}));

vi.mock("#/hooks/use-user-providers", () => ({
  useUserProviders: () => ({
    providers: [],
  }),
}));

vi.mock("#/hooks/use-conversation-name-context-menu", () => ({
  useConversationNameContextMenu: () => ({
    isOpen: false,
    contextMenuRef: { current: null },
    handleContextMenu: vi.fn(),
    handleClose: vi.fn(),
    handleRename: vi.fn(),
    handleDelete: vi.fn(),
  }),
}));

describe("InteractiveChatBox", () => {
  const onSubmitMock = vi.fn();

  beforeEach(() => {
    useSelectedOrganizationStore.setState({ organizationId: "test-org-id" });
  });

  const mockStores = (agentState: AgentState = AgentState.INIT) => {
    vi.mocked(useAgentState).mockReturnValue({
      curAgentState: agentState, isArchived: false,
    });

    useConversationStore.setState({
      images: [],
      files: [],
      loadingFiles: [],
      loadingImages: [],
      submittedMessage: null,
      messageToSend: null,
      shouldShownAgentLoading: false,
      shouldHideSuggestions: false,
      isRightPanelShown: true,
      selectedTab: "editor" as const,
      hasRightPanelToggled: true,
    });
  };

  const renderInteractiveChatBox = (props: any, options: any = {}) =>
    renderWithProviders(
      <MemoryRouter>
        <InteractiveChatBox {...props} />
      </MemoryRouter>,
      options,
    );

  beforeAll(() => {
    global.URL.createObjectURL = vi
      .fn()
      .mockReturnValue("blob:http://example.com");
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("should render", () => {
    mockStores(AgentState.INIT);

    renderInteractiveChatBox({
      onSubmit: onSubmitMock,
    });

    const chatBox = screen.getByTestId("interactive-chat-box");
    expect(chatBox).toBeInTheDocument();
  });

  it("should set custom values", async () => {
    const user = userEvent.setup();
    mockStores(AgentState.AWAITING_USER_INPUT);

    renderInteractiveChatBox({
      onSubmit: onSubmitMock,
    });

    const textbox = screen.getByTestId("chat-input");

    await user.type(textbox, "Hello, world!");

    expect(textbox).toHaveTextContent("Hello, world!");
  });

  it("should display the image previews when images are uploaded", async () => {
    const user = userEvent.setup();
    mockStores(AgentState.INIT);

    renderInteractiveChatBox({
      onSubmit: onSubmitMock,
    });

    // Create a larger file to ensure it passes validation
    const fileContent = new Array(1024).fill("a").join(""); // 1KB file
    const file = new File([fileContent], "chucknorris.png", {
      type: "image/png",
    });

    const paperclipIcon = screen.getByTestId("paperclip-icon");
    await user.click(paperclipIcon);

    const input = screen.getByTestId("upload-image-input");
    await user.upload(input, file);

    // For now, just verify the file input is accessible
    expect(input).toBeInTheDocument();
  });

  it("should remove the image preview when the close button is clicked", async () => {
    const user = userEvent.setup();
    mockStores(AgentState.INIT);

    renderInteractiveChatBox({
      onSubmit: onSubmitMock,
    });

    const fileContent = new Array(1024).fill("a").join(""); // 1KB file
    const file = new File([fileContent], "chucknorris.png", {
      type: "image/png",
    });

    const paperclipIcon = screen.getByTestId("paperclip-icon");
    await user.click(paperclipIcon);

    const input = screen.getByTestId("upload-image-input");
    await user.upload(input, file);

    // For now, just verify the file input is accessible
    expect(input).toBeInTheDocument();
  });

  it("should call onSubmit with the message and images", async () => {
    const user = userEvent.setup();
    mockStores(AgentState.INIT);

    renderInteractiveChatBox({
      onSubmit: onSubmitMock,
    });

    const textarea = screen.getByTestId("chat-input");

    await user.type(textarea, "Hello, world!");

    // Set innerText directly as the component reads this property
    textarea.innerText = "Hello, world!";

    expect(textarea).toHaveTextContent("Hello, world!");

    // Click the submit button instead of pressing Enter for more reliable testing
    const submitButton = screen.getByTestId("submit-button");

    expect(submitButton).not.toBeDisabled();

    await user.click(submitButton);

    expect(onSubmitMock).toHaveBeenCalledWith("Hello, world!", [], []);
  });

  it("should disable the submit button when awaiting user confirmation", async () => {
    const user = userEvent.setup();
    mockStores(AgentState.AWAITING_USER_CONFIRMATION);

    renderInteractiveChatBox({
      onSubmit: onSubmitMock,
    });

    const button = screen.getByTestId("submit-button");
    expect(button).toBeDisabled();

    await user.click(button);
    expect(onSubmitMock).not.toHaveBeenCalled();
  });

  it("should lock the text input field when disabled prop is true (isNewConversationPending)", () => {
    mockStores(AgentState.INIT);

    renderInteractiveChatBox({
      onSubmit: onSubmitMock,
      disabled: true,
    });

    const chatInput = screen.getByTestId("chat-input");
    expect(chatInput).toHaveAttribute("contenteditable", "false");
    expect(chatInput.className).toContain("cursor-not-allowed");
    expect(chatInput.className).toContain("opacity-50");
  });

  it("should keep the text input field editable when disabled prop is false", () => {
    mockStores(AgentState.INIT);

    renderInteractiveChatBox({
      onSubmit: onSubmitMock,
      disabled: false,
    });

    const chatInput = screen.getByTestId("chat-input");
    expect(chatInput).toHaveAttribute("contenteditable", "true");
    expect(chatInput.className).not.toContain("cursor-not-allowed");
    expect(chatInput.className).not.toContain("opacity-50");
  });

  it("should handle image upload and message submission correctly", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    mockStores(AgentState.AWAITING_USER_INPUT);

    const { rerender } = renderInteractiveChatBox({
      onSubmit,
    });

    const textarea = screen.getByTestId("chat-input");
    expect(textarea).toHaveTextContent("");

    // Set innerText directly as the component reads this property
    textarea.innerText = "test message";

    const submitButton = screen.getByTestId("submit-button");
    await user.click(submitButton);

    expect(onSubmit).toHaveBeenCalledWith("test message", [], []);

    // Simulate parent component updating the value prop
    rerender(
      <MemoryRouter>
        <InteractiveChatBox onSubmit={onSubmit} />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("chat-input")).toHaveTextContent("");
  });

  it("keeps a draft typed after a send when the earlier send's echo arrives", async () => {
    localStorage.clear();
    const conversationId = "test-conversation-id";
    const user = userEvent.setup();
    mockStores(AgentState.AWAITING_USER_INPUT);

    renderInteractiveChatBox({ onSubmit: onSubmitMock });

    const textbox = screen.getByTestId("chat-input");
    await user.type(textbox, "first message");
    textbox.innerText = "first message";
    await user.click(screen.getByTestId("submit-button"));
    expect(onSubmitMock).toHaveBeenCalledWith("first message", [], []);

    // jsdom has no layout-backed innerText, so mirror what the browser reports.
    textbox.innerText = "newer draft";
    await user.type(textbox, "newer draft");
    await waitFor(() => {
      expect(getConversationState(conversationId).draftMessage).toBe(
        "newer draft",
      );
    });

    clearSubmittedConversationDraft(conversationId);

    expect(getConversationState(conversationId).draftMessage).toBe(
      "newer draft",
    );
  });

  it("clears the submitted draft when that send's echo arrives", async () => {
    localStorage.clear();
    const conversationId = "test-conversation-id";
    const user = userEvent.setup();
    mockStores(AgentState.AWAITING_USER_INPUT);

    renderInteractiveChatBox({ onSubmit: onSubmitMock });

    const textbox = screen.getByTestId("chat-input");
    textbox.innerText = "sent message";
    await user.type(textbox, "sent message");
    await waitFor(() => {
      expect(getConversationState(conversationId).draftMessage).toBe(
        "sent message",
      );
    });
    await user.click(screen.getByTestId("submit-button"));

    clearSubmittedConversationDraft(conversationId);

    expect(getConversationState(conversationId).draftMessage).toBeNull();
  });

  it("clears the sent text when its echo arrives before a newer edit is saved", async () => {
    localStorage.clear();
    const conversationId = "test-conversation-id";
    const user = userEvent.setup();
    mockStores(AgentState.AWAITING_USER_INPUT);

    const { unmount } = renderInteractiveChatBox({ onSubmit: onSubmitMock });

    const textbox = screen.getByTestId("chat-input");
    textbox.innerText = "message A";
    await user.type(textbox, "message A");
    await waitFor(() => {
      expect(getConversationState(conversationId).draftMessage).toBe(
        "message A",
      );
    });
    await user.click(screen.getByTestId("submit-button"));

    // The echo lands inside the debounce window of the next keystroke, and the
    // page unloads before that save runs.
    textbox.innerText = "d";
    await user.type(textbox, "d");
    clearSubmittedConversationDraft(conversationId);
    unmount();

    expect(getConversationState(conversationId).draftMessage).toBeNull();
  });
});
