import { within, screen, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { idpService } from "#/api/idp-service/idp-service.api";
import { MintSignupLinkModal } from "#/components/features/org/mint-signup-link-modal";
import { SUPER_ADMIN_SETUP_STEP_EVENT } from "#/components/features/setup/tours/types";

const renderMintSignupLinkModal = () =>
  render(<MintSignupLinkModal orgId="1" onClose={vi.fn()} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={new QueryClient()}>
        {children}
      </QueryClientProvider>
    ),
  });

describe("MintSignupLinkModal", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("should tell the setup guide when a sign-up link is created", async () => {
    vi.spyOn(idpService, "createSignupLink").mockResolvedValue({
      url: "https://example.test/oauth/idp/invite?token=abc",
      expires_at: "2026-10-14T00:00:00Z",
      role: "member",
    });
    const steps: string[] = [];
    const onStep = (event: Event) =>
      steps.push((event as CustomEvent<{ id: string }>).detail.id);
    window.addEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, onStep);

    renderMintSignupLinkModal();
    const modal = screen.getByTestId("mint-signup-link-modal");
    await userEvent.type(
      within(modal).getByTestId("signup-link-email-input"),
      "someone@acme.org",
    );
    await userEvent.click(
      within(modal).getByRole("button", { name: /create/i }),
    );

    await waitFor(() => expect(steps).toEqual(["invite-users"]));
    expect(screen.getByTestId("signup-link-result")).toHaveTextContent(
      "someone@acme.org",
    );
    window.removeEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, onStep);
  });
});
