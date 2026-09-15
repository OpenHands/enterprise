import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";

describe("IntegrationProviderIcon", () => {
  it("renders the known Slack brand mark", () => {
    render(<IntegrationProviderIcon provider="slack" />);
    expect(
      screen.getByTestId("integration-provider-icon-slack"),
    ).toBeInTheDocument();
    expect(screen.queryByText("S")).not.toBeInTheDocument();
  });

  it("renders a catalog glyph for official Hub slugs", () => {
    render(<IntegrationProviderIcon provider="notion" />);
    const badge = screen.getByTestId("integration-provider-icon-notion");
    expect(badge).toBeInTheDocument();
    expect(badge).not.toHaveTextContent("N");
    expect(badge.querySelector("svg")).toBeTruthy();
    expect(badge).toHaveStyle({
      backgroundColor: "#FFFFFF",
      color: "#000000",
    });
  });

  it("uses a remote catalog mark when only a logo URL is available", () => {
    render(
      <IntegrationProviderIcon
        provider="ordinal"
        logoUrl="https://app.tryordinal.com/favicon.ico"
      />,
    );
    const image = screen
      .getByTestId("integration-provider-icon-ordinal")
      .querySelector("img");
    expect(image).toHaveAttribute(
      "src",
      "https://app.tryordinal.com/favicon.ico",
    );
  });
});
