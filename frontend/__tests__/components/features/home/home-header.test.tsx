import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import React, { type ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { HomeHeader } from "#/components/features/home/home-header/home-header";

vi.mock("react-i18next", async () => {
  const actual = await vi.importActual("react-i18next");
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string) => {
        const translations: Record<string, string> = {
          COMMON$CLICK_HERE: "Click here",
          HOME$AGENT_CANVAS_BANNER:
            "New User Experience! Please visit <link>app.all-hands.dev/canvas</link> to try out OpenHands Cloud with Agent Canvas.",
          HOME$GUIDE_MESSAGE_TITLE:
            "New around here? Not sure where to start?",
          HOME$LETS_START_BUILDING: "Let's start building",
        };
        return translations[key] || key;
      },
      i18n: { language: "en" },
    }),
    Trans: ({
      components,
    }: {
      components: { link: React.ReactElement };
    }) => (
      <>
        New User Experience! Please visit{" "}
        {React.cloneElement(components.link, {}, "app.all-hands.dev/canvas")} to
        try out OpenHands Cloud with Agent Canvas.
      </>
    ),
  };
});

const renderHomeHeader = (props?: ComponentProps<typeof HomeHeader>) => {
  return render(<HomeHeader {...props} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={new QueryClient()}>
        {children}
      </QueryClientProvider>
    ),
  });
};

describe("HomeHeader", () => {
  it("should render the header with the correct title", () => {
    renderHomeHeader();

    const title = screen.getByText("Let's start building");
    expect(title).toBeInTheDocument();
  });

  it("should render the GuideMessage component by default", () => {
    renderHomeHeader();

    expect(
      screen.getByText("New around here? Not sure where to start?"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("agent-canvas-banner")).not.toBeInTheDocument();
  });

  it("should render the Agent Canvas banner when enabled", () => {
    renderHomeHeader({ showAgentCanvasBanner: true });

    expect(screen.getByTestId("agent-canvas-banner")).toBeInTheDocument();
    expect(
      screen.queryByText("New around here? Not sure where to start?"),
    ).not.toBeInTheDocument();

    const link = screen.getByRole("link", {
      name: "app.all-hands.dev/canvas",
    });
    expect(link).toHaveAttribute("href", "https://app.all-hands.dev/canvas");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveClass("cursor-pointer", "underline", "underline-offset-4");
  });

  it("should have the correct CSS classes for layout", () => {
    renderHomeHeader();

    const header = screen.getByRole("banner");
    expect(header).toHaveClass("flex", "flex-col", "items-center");
  });
});
