import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { SettingsTextarea } from "#/components/features/settings/settings-textarea";

describe("SettingsTextarea", () => {
  it("should render a textarea element, not a single-line input", () => {
    render(<SettingsTextarea testId="test-textarea" label="Test Textarea" />);

    expect(screen.getByTestId("test-textarea").tagName).toBe("TEXTAREA");
  });

  it("should render an optional tag if showOptionalTag is true", async () => {
    const { rerender } = render(
      <SettingsTextarea testId="test-textarea" label="Test Textarea" />,
    );

    expect(screen.queryByText(/optional/i)).not.toBeInTheDocument();

    rerender(
      <SettingsTextarea
        testId="test-textarea"
        showOptionalTag
        label="Test Textarea"
      />,
    );

    expect(screen.getByText(/optional/i)).toBeInTheDocument();
  });

  it("should disable the textarea if isDisabled is true", () => {
    render(
      <SettingsTextarea
        testId="test-textarea"
        label="Test Textarea"
        isDisabled
      />,
    );

    expect(screen.getByTestId("test-textarea")).toBeDisabled();
  });

  it("should mark the textarea read-only when isReadOnly is true", () => {
    render(
      <SettingsTextarea
        testId="test-textarea"
        label="Test Textarea"
        isReadOnly
      />,
    );

    expect(screen.getByTestId("test-textarea")).toHaveProperty(
      "readOnly",
      true,
    );
  });

  it("should set a default value on the textarea", () => {
    render(
      <SettingsTextarea
        testId="test-textarea"
        label="Test Textarea"
        defaultValue="Test Value"
      />,
    );

    expect(screen.getByTestId("test-textarea")).toHaveValue("Test Value");
  });

  it("preserves embedded newlines, unlike a single-line input", () => {
    const multilineValue = "line one\nline two\nline three";

    render(
      <SettingsTextarea
        testId="test-textarea"
        label="Test Textarea"
        value={multilineValue}
        onChange={() => {}}
      />,
    );

    expect(screen.getByTestId("test-textarea")).toHaveValue(multilineValue);
  });

  it("should call onChange with the textarea value", async () => {
    const onChangeMock = vi.fn();
    const user = userEvent.setup();

    render(
      <SettingsTextarea
        testId="test-textarea"
        label="Test Textarea"
        onChange={onChangeMock}
      />,
    );

    const textarea = screen.getByTestId("test-textarea");
    await user.type(textarea, "Hi");

    expect(onChangeMock).toHaveBeenCalledTimes(2);
    expect(onChangeMock).toHaveBeenNthCalledWith(2, "Hi");
  });

  it("should show a validation error when error is set", () => {
    render(
      <SettingsTextarea
        testId="test-textarea"
        label="Test Textarea"
        error="Invalid value"
      />,
    );

    expect(screen.getByTestId("test-textarea")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByTestId("test-textarea-error")).toHaveTextContent(
      "Invalid value",
    );
  });
});
