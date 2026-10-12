import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ProfileActionsMenu } from "#/components/features/settings/profile-actions-menu";

function createAnchorRef() {
  const anchor = document.createElement("button");
  anchor.getBoundingClientRect = () =>
    ({
      top: 100,
      bottom: 120,
      left: 200,
      right: 220,
      width: 20,
      height: 20,
      x: 200,
      y: 100,
      toJSON: () => ({}),
    }) as DOMRect;
  document.body.appendChild(anchor);
  return {
    ref: { current: anchor } as React.RefObject<HTMLElement | null>,
    cleanup: () => anchor.remove(),
  };
}

function renderMenu(
  overrides: Partial<React.ComponentProps<typeof ProfileActionsMenu>> = {},
) {
  const { ref: anchorRef, cleanup } = createAnchorRef();
  const props = {
    onEdit: vi.fn(),
    onRename: vi.fn(),
    onSetActive: vi.fn(),
    onDelete: vi.fn(),
    onClose: vi.fn(),
    isActive: false,
    isActivating: false,
    anchorRef,
    ...overrides,
  };
  const result = {
    // eslint-disable-next-line react/jsx-props-no-spreading
    ...render(<ProfileActionsMenu {...props} />),
    props,
    cleanup,
  };
  return result;
}

describe("ProfileActionsMenu", () => {
  it("renders edit, rename, set-active, and delete items", () => {
    const { cleanup } = renderMenu();

    expect(screen.getByTestId("profile-edit")).toBeInTheDocument();
    expect(screen.getByTestId("profile-rename")).toBeInTheDocument();
    expect(screen.getByTestId("profile-set-active")).toBeInTheDocument();
    expect(screen.getByTestId("profile-delete")).toBeInTheDocument();
    cleanup();
  });

  it("invokes the matching callback then onClose when an item is clicked", async () => {
    const { props, cleanup } = renderMenu();
    const user = userEvent.setup();

    await user.click(screen.getByTestId("profile-edit"));
    expect(props.onEdit).toHaveBeenCalledTimes(1);
    expect(props.onClose).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it("disables Set as default when the profile is already default", () => {
    const { cleanup } = renderMenu({ isActive: true });

    expect(screen.getByTestId("profile-set-active")).toBeDisabled();
    cleanup();
  });

  it("disables Set as default while an activation is in flight", () => {
    const { cleanup } = renderMenu({ isActivating: true });

    expect(screen.getByTestId("profile-set-active")).toBeDisabled();
    cleanup();
  });
});
