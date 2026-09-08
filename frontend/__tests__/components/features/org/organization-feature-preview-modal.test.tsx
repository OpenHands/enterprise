import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OrganizationFeaturePreviewModal } from "#/components/features/org/organization-feature-preview-modal";

const { posthogCaptureMock } = vi.hoisted(() => ({
  posthogCaptureMock: vi.fn(),
}));

vi.mock("posthog-js/react", () => ({
  usePostHog: () => ({
    capture: posthogCaptureMock,
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        ORG$FEATURE_PREVIEW_TITLE: "Available in OpenHands Enterprise",
        ORG$FEATURE_PREVIEW_DESCRIPTION:
          "Organizations include team member management, shared settings, usage monitoring, and budget controls.",
        ORG$FEATURE_PREVIEW_USAGE: "Usage & monitoring",
        SETTINGS$NAV_BUDGETS: "Budgets",
        ORG$TRY_IT_OUT: "Try it out",
        BUTTON$CLOSE: "Close",
      })[key] ?? key,
  }),
}));

describe("OrganizationFeaturePreviewModal", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("opens the enterprise quick-start docs and tracks the CTA click", async () => {
    const openMock = vi.fn();
    vi.stubGlobal("open", openMock);

    render(<OrganizationFeaturePreviewModal onClose={vi.fn()} />);

    expect(
      screen.getByRole("heading", {
        name: "Available in OpenHands Enterprise",
      }),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Try it out" }));

    expect(posthogCaptureMock).toHaveBeenCalledWith("enterprise cta clicked", {
      location: "organization_feature_preview_modal_try_it_out",
    });
    expect(openMock).toHaveBeenCalledWith(
      "https://docs.openhands.dev/enterprise/quick-start",
      "_blank",
      "noopener",
    );
  });
});
