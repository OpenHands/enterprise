import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InstallFooter } from "#/components/features/super-admin/install-footer";

describe("InstallFooter", () => {
  it("links the Terms of Service and Privacy Policy on openhands.dev", () => {
    // Arrange & Act
    render(<InstallFooter />);

    // Assert
    const terms = screen.getByRole("link", { name: "SA_NUX$FOOTER_TERMS" });
    const privacy = screen.getByRole("link", { name: "SA_NUX$FOOTER_PRIVACY" });

    expect(terms).toHaveAttribute("href", "https://www.openhands.dev/tos");
    expect(privacy).toHaveAttribute(
      "href",
      "https://www.openhands.dev/privacy",
    );
    expect(terms).toHaveAttribute("target", "_blank");
    expect(privacy).toHaveAttribute("rel", "noopener noreferrer");
  });
});
