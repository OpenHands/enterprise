import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsDesktopSidebar } from "#/components/features/settings/settings-desktop-sidebar";
import { SIDEBAR_WIDTH_STORAGE_KEY } from "#/hooks/use-resizable-sidebar-width";

vi.mock("#/components/features/settings/settings-nav-brand", () => ({
  SettingsNavBrand: () => <div data-testid="settings-nav-brand" />,
}));

vi.mock("#/components/features/settings/settings-nav-items", () => ({
  SettingsNavItems: () => <nav data-testid="settings-nav-items" />,
}));

vi.mock("#/components/features/settings/settings-nav-user-menu", () => ({
  SettingsNavUserMenu: () => <div data-testid="settings-nav-user-menu" />,
}));

const renderRail = () =>
  render(
    <MemoryRouter>
      <SettingsDesktopSidebar navigationItems={[]} />
    </MemoryRouter>,
  );

const railWidth = () =>
  screen
    .getByTestId("settings-navbar-desktop")
    .style.getPropertyValue("--sidebar-width");

describe("SettingsDesktopSidebar", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => window.localStorage.clear());

  it("opens at the width saved by the agent-canvas sidebar", () => {
    // Canvas stores the width with the same key on the same origin.
    window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, "420");

    renderRail();

    expect(railWidth()).toBe("420px");
  });

  it("resizes by dragging its edge within the canvas limits and saves the width", () => {
    // Arrange: the rail starts at the left edge of the viewport.
    const { unmount } = renderRail();
    expect(railWidth()).toBe("300px");
    vi.spyOn(
      screen.getByTestId("settings-navbar-desktop"),
      "getBoundingClientRect",
    ).mockReturnValue(DOMRect.fromRect({ x: 0, width: 300, height: 800 }));
    const handle = screen
      .getByTestId("settings-rail-resize-handle")
      .querySelector(".cursor-ew-resize")!;

    // Act + Assert: the width follows the pointer and stops at 240px and 560px.
    fireEvent.mouseDown(handle);
    for (const [clientX, expected] of [
      [100, "240px"],
      [2000, "560px"],
      [380, "380px"],
    ] as const) {
      fireEvent.mouseMove(document, { clientX });
      expect(railWidth()).toBe(expected);
    }
    fireEvent.mouseUp(document);

    // The width is saved where agent-canvas reads it, and survives a remount.
    expect(window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY)).toBe("380");
    unmount();
    renderRail();
    expect(railWidth()).toBe("380px");
  });
});
