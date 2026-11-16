import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "test-utils";
import { MarketplaceTable } from "#/components/features/settings/skills-settings/marketplace-table";

describe("MarketplaceTable", () => {
  it("sizes the Scope column to fit the widest scope badge", () => {
    // Act
    renderWithProviders(
      <MarketplaceTable
        marketplaces={[
          {
            name: "extensions-production",
            source: "github:OpenHands/extensions",
            auto_load: true,
            scope: "org",
          },
        ]}
        onToggleAutoLoad={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        canEdit={() => true}
        getAutoLoadTitle={() => undefined}
        isAdminOrOwner
      />,
    );

    // Assert
    const cols = screen.getByRole("table").querySelectorAll("colgroup col");
    // Repository takes the remaining width so a fixed Scope column fits.
    expect(cols[1].className).toBe("");
    expect(cols[4]).toHaveClass("w-32");
  });
});
