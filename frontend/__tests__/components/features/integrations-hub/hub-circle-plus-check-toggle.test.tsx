import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HubCirclePlusCheckToggle } from "#/components/features/integrations-hub/hub-circle-plus-check-toggle";

describe("HubCirclePlusCheckToggle", () => {
  it("renders an unselected circular plus switch", () => {
    render(
      <HubCirclePlusCheckToggle
        testId="integrations-hub-connect-github"
        isSelected={false}
        onToggle={vi.fn()}
        enableLabel="Connect"
        disableLabel="Connect"
        enableTooltip="Connect"
        removeTooltip="Disable"
      />,
    );

    const toggle = screen.getByTestId("integrations-hub-connect-github");
    expect(toggle).toHaveAttribute("role", "switch");
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(toggle).toHaveAttribute("aria-label", "Connect");
    expect(toggle.className).toContain("rounded-full");
    expect(toggle.className).toContain("size-7");
    expect(toggle.className).toContain("bg-surface-raised");
  });

  it("toggles selection and shows a check when selected", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();

    const { rerender } = render(
      <HubCirclePlusCheckToggle
        testId="integrations-hub-toggle-slack"
        isSelected={false}
        onToggle={onToggle}
        enableLabel="Enable"
        disableLabel="Disable"
        enableTooltip="Enable"
        disableTooltip="Disable"
        removeTooltip="Disable"
      />,
    );

    await user.click(screen.getByTestId("integrations-hub-toggle-slack"));
    expect(onToggle).toHaveBeenCalledWith(true);

    rerender(
      <HubCirclePlusCheckToggle
        testId="integrations-hub-toggle-slack"
        isSelected
        onToggle={onToggle}
        enableLabel="Enable"
        disableLabel="Disable"
        enableTooltip="Enable"
        disableTooltip="Disable"
        removeTooltip="Disable"
      />,
    );

    const toggle = screen.getByTestId("integrations-hub-toggle-slack");
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(toggle).toHaveAttribute("aria-label", "Disable");
    expect(toggle.querySelector("svg")).toHaveClass("lucide-check");

    await user.unhover(toggle);
    await user.hover(toggle);
    expect(toggle.querySelector("svg")).toHaveClass("lucide-x");

    await user.unhover(toggle);
    expect(toggle.querySelector("svg")).toHaveClass("lucide-check");
  });
});
