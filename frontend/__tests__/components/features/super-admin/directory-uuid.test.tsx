import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DirectoryUuid } from "#/components/features/super-admin/directory-uuid";
import * as ToastHandlers from "#/utils/custom-toast-handlers";

const UUID = "12345678-1234-4234-8234-123456789abc";

afterEach(() => vi.restoreAllMocks());

describe("DirectoryUuid", () => {
  it("copies the full UUID by keyboard without activating the containing row", async () => {
    const user = userEvent.setup();
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue();
    const onRowClick = vi.fn();
    render(
      <div onClick={onRowClick}>
        <DirectoryUuid uuid={UUID} />
      </div>,
    );

    await user.tab();
    await user.keyboard("{Enter}");

    expect(writeText).toHaveBeenCalledWith(UUID);
    expect(onRowClick).not.toHaveBeenCalled();
    expect(screen.getByTitle(UUID)).toHaveTextContent(UUID);
    expect(
      screen.getByRole("button", { name: "BUTTON$COPIED" }),
    ).toBeDisabled();
  });

  it("reports a clipboard failure and allows a retry", async () => {
    const user = userEvent.setup();
    const writeText = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockRejectedValueOnce(new Error("Clipboard unavailable"))
      .mockResolvedValue();
    const errorToast = vi
      .spyOn(ToastHandlers, "displayErrorToast")
      .mockImplementation(() => undefined);
    render(<DirectoryUuid uuid={UUID} />);

    await user.click(screen.getByRole("button"));
    await waitFor(() =>
      expect(errorToast).toHaveBeenCalledWith(
        "CHAT_INTERFACE$CHAT_MESSAGE_COPY_FAILED",
      ),
    );
    expect(screen.getByRole("button")).toBeEnabled();
    await user.click(screen.getByRole("button"));
    expect(writeText).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it("restores the copy control after the feedback timeout", async () => {
    userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    vi.useFakeTimers();
    try {
      render(<DirectoryUuid uuid={UUID} />);
      await act(async () => {
        fireEvent.click(screen.getByRole("button"));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(screen.getByRole("button")).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });
});
