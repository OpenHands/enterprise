import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { HubCatalogViewToggle } from "#/components/features/integrations-hub/hub-catalog-view-toggle";
import type { HubCatalogView } from "#/components/features/integrations-hub/hub-catalog-view-toggle";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

function ToggleHarness() {
  const [view, setView] = useState<HubCatalogView>("list");
  return <HubCatalogViewToggle view={view} onChange={setView} />;
}

describe("HubCatalogViewToggle", () => {
  it("opens a menu from the icon trigger and switches to card view", async () => {
    const user = userEvent.setup();
    render(<ToggleHarness />);

    const trigger = screen.getByTestId("admin-catalog-view-toggle");
    expect(trigger).toHaveClass("size-9");
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(
      screen.queryByTestId("admin-catalog-view-cards"),
    ).not.toBeInTheDocument();

    await user.click(trigger);
    await user.click(screen.getByTestId("admin-catalog-view-cards"));

    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);
    expect(screen.getByTestId("admin-catalog-view-list")).toBeInTheDocument();
    expect(
      screen.getByTestId("admin-catalog-view-cards"),
    ).toBeInTheDocument();
  });
});
