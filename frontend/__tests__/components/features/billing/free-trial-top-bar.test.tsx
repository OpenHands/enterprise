import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { FreeTrialTopBar } from "#/components/features/billing/free-trial-top-bar";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      options?.count !== undefined ? `${key}:${options.count}` : key,
  }),
}));

describe("FreeTrialTopBar", () => {
  it("renders enterprise free trial label, days left, and action buttons", () => {
    render(
      <MemoryRouter>
        <FreeTrialTopBar daysLeft={12} />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("free-trial-top-bar")).toBeInTheDocument();
    expect(screen.getByTestId("free-trial-days-left")).toHaveTextContent(
      "FREE_TRIAL$DAYS_LEFT:12",
    );
    expect(screen.getByTestId("free-trial-choose-plan")).toHaveAttribute(
      "href",
      "https://openhands.dev/pricing",
    );
    expect(screen.getByTestId("free-trial-contact-sales")).toHaveAttribute(
      "href",
      "/information-request",
    );
  });
});
