import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "test-utils";
import { SandboxStatusIndicator } from "#/components/features/home/recent-conversations/sandbox-status-indicator";
import { SandboxStatusBadges } from "#/components/features/conversation-panel/conversation-card/sandbox-status-badges";

vi.mock("react-i18next", async () => ({
  ...(await vi.importActual("react-i18next")),
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe("sandbox availability in conversation lists", () => {
  it("labels UNKNOWN as temporarily unavailable without an archive badge", () => {
    renderWithProviders(
      <>
        <SandboxStatusIndicator sandboxStatus="UNKNOWN" />
        <SandboxStatusBadges sandboxStatus="UNKNOWN" />
      </>,
    );
    expect(
      screen.getByLabelText("SANDBOX$TEMPORARILY_UNAVAILABLE"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("SANDBOX$TEMPORARILY_UNAVAILABLE"),
    ).toBeInTheDocument();
    expect(screen.queryByText("COMMON$ARCHIVED")).not.toBeInTheDocument();
    expect(screen.queryByText("COMMON$STOPPED")).not.toBeInTheDocument();
  });

  it("continues to label a confirmed missing sandbox as archived", () => {
    renderWithProviders(<SandboxStatusBadges sandboxStatus="MISSING" />);
    expect(screen.getByText("COMMON$ARCHIVED")).toBeInTheDocument();
    expect(
      screen.queryByText("SANDBOX$TEMPORARILY_UNAVAILABLE"),
    ).not.toBeInTheDocument();
  });
});
