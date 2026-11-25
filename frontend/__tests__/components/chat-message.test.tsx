import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { ChatMessage } from "#/components/features/chat/chat-message";
import { I18nKey } from "#/i18n/declaration";
import * as toastHandlers from "#/utils/custom-toast-handlers";

describe("ChatMessage", () => {
  it("should render a user message", () => {
    render(<ChatMessage type="user" message="Hello, World!" />);
    expect(screen.getByTestId("user-message")).toBeInTheDocument();
    expect(screen.getByText("Hello, World!")).toBeInTheDocument();
  });

  it("should support code syntax highlighting", () => {
    const code = "```js\nconsole.log('Hello, World!')\n```";
    render(<ChatMessage type="user" message={code} />);

    // SyntaxHighlighter breaks the code blocks into "tokens"
    expect(screen.getByText("console")).toBeInTheDocument();
    expect(screen.getByText("log")).toBeInTheDocument();
    expect(screen.getByText("'Hello, World!'")).toBeInTheDocument();
  });

  it("should render the copy to clipboard button when the user hovers over the message", async () => {
    const user = userEvent.setup();
    render(<ChatMessage type="user" message="Hello, World!" />);
    const message = screen.getByText("Hello, World!");

    expect(screen.getByTestId("copy-to-clipboard")).not.toBeVisible();

    await user.hover(message);

    expect(screen.getByTestId("copy-to-clipboard")).toBeVisible();
  });

  it("should copy content to clipboard", async () => {
    const user = userEvent.setup();
    render(<ChatMessage type="user" message="Hello, World!" />);
    const copyToClipboardButton = screen.getByTestId("copy-to-clipboard");

    await user.click(copyToClipboardButton);

    await waitFor(() =>
      expect(navigator.clipboard.readText()).resolves.toBe("Hello, World!"),
    );
  });

  it("should copy content without the Clipboard API and show the copied state", async () => {
    const user = userEvent.setup();
    const originalClipboard = navigator.clipboard;
    const originalExecCommand = document.execCommand;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      writable: true,
      value: undefined,
    });
    const execCommand = vi.fn().mockReturnValue(true);
    document.execCommand = execCommand;

    try {
      render(<ChatMessage type="user" message="Hello, World!" />);
      await user.click(screen.getByTestId("copy-to-clipboard"));

      await waitFor(() =>
        expect(screen.getByTestId("copy-to-clipboard")).toHaveAttribute(
          "aria-label",
          "BUTTON$COPIED",
        ),
      );
      expect(execCommand).toHaveBeenCalledWith("copy");
    } finally {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        writable: true,
        value: originalClipboard,
      });
      document.execCommand = originalExecCommand;
    }
  });

  it("should show an error instead of the copied state when the copy fails", async () => {
    const displayErrorToast = vi
      .spyOn(toastHandlers, "displayErrorToast")
      .mockImplementation(() => {});
    const user = userEvent.setup();
    const originalClipboard = navigator.clipboard;
    const originalExecCommand = document.execCommand;
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      writable: true,
      value: undefined,
    });
    document.execCommand = vi.fn().mockReturnValue(false);

    try {
      render(<ChatMessage type="user" message="Hello, World!" />);
      await user.click(screen.getByTestId("copy-to-clipboard"));

      await waitFor(() =>
        expect(displayErrorToast).toHaveBeenCalledWith(
          I18nKey.CHAT_INTERFACE$CHAT_MESSAGE_COPY_FAILED,
        ),
      );
      expect(screen.getByTestId("copy-to-clipboard")).toHaveAttribute(
        "aria-label",
        "BUTTON$COPY",
      );
    } finally {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        writable: true,
        value: originalClipboard,
      });
      document.execCommand = originalExecCommand;
      displayErrorToast.mockRestore();
    }
  });

  it("should render a component passed as a prop", () => {
    function Component() {
      return <div data-testid="custom-component">Custom Component</div>;
    }
    render(
      <ChatMessage type="user" message="Hello, World">
        <Component />
      </ChatMessage>,
    );
    expect(screen.getByTestId("custom-component")).toBeInTheDocument();
  });

  it("should apply correct styles to inline code", () => {
    render(
      <ChatMessage type="agent" message="Here is some `inline code` text" />,
    );
    const codeElement = screen.getByText("inline code");

    expect(codeElement.tagName.toLowerCase()).toBe("code");
    expect(codeElement.closest("article")).not.toBeNull();
  });
});
