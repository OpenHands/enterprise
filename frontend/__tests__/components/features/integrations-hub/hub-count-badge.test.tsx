import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HubCountBadge } from "#/components/features/integrations-hub/hub-count-badge";

describe("HubCountBadge", () => {
  it("renders a circular raised-surface count pill", () => {
    render(<HubCountBadge count={3} />);

    const badge = screen.getByText("3");
    expect(badge.className).toContain("rounded-full");
    expect(badge.className).toContain("bg-surface-raised");
    expect(badge.className).toContain("justify-center");
  });
});
