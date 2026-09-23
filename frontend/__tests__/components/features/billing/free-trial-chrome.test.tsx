import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { FreeTrialChrome } from "#/components/features/billing/free-trial-chrome";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      options?.count !== undefined ? `${key}:${options.count}` : key,
  }),
}));

describe("FreeTrialChrome", () => {
  it("shows the top bar while the trial is active", () => {
    render(
      <MemoryRouter>
        <FreeTrialChrome daysLeft={12} />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("free-trial-top-bar")).toBeInTheDocument();
    expect(
      screen.queryByTestId("free-trial-expired-modal"),
    ).not.toBeInTheDocument();
  });

  it("keeps the top bar and shows the expired modal when the trial is done", () => {
    render(
      <MemoryRouter>
        <FreeTrialChrome daysLeft={0} />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("free-trial-top-bar")).toBeInTheDocument();
    expect(screen.getByTestId("free-trial-days-left")).toHaveTextContent(
      "FREE_TRIAL$DAYS_LEFT:0",
    );
    expect(screen.getByTestId("free-trial-expired-modal")).toBeInTheDocument();
    expect(
      screen.getByTestId("free-trial-expired-choose-plan"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("free-trial-expired-contact-sales"),
    ).toBeInTheDocument();
  });
});
