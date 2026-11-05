import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { InvitationEmailMismatchModal } from "#/components/features/org/invitation-email-mismatch-modal";
import { useInvitationEmailMismatchStore } from "#/stores/invitation-email-mismatch-store";

const INVITATION_TOKEN_KEY = "openhands_invitation_token";

const { mockMe, mockLogout } = vi.hoisted(() => ({
  mockMe: { data: { email: "b@example.com" } as { email?: string } | null },
  mockLogout: vi.fn(),
}));

vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => mockMe,
}));

vi.mock("#/hooks/mutation/use-logout", () => ({
  useLogout: () => ({ mutate: mockLogout, isPending: false }),
}));

function renderModal() {
  return render(
    <MemoryRouter>
      <InvitationEmailMismatchModal />
    </MemoryRouter>,
  );
}

describe("InvitationEmailMismatchModal", () => {
  beforeEach(() => {
    localStorage.setItem(INVITATION_TOKEN_KEY, "inv-token");
    useInvitationEmailMismatchStore.getState().open();
    mockMe.data = { email: "b@example.com" };
  });

  afterEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useInvitationEmailMismatchStore.getState().close();
  });

  it("explains the mismatch with the signed-in email", () => {
    // Act
    renderModal();

    // Assert
    const modal = screen.getByTestId("invitation-email-mismatch-modal");
    expect(modal).toHaveTextContent("ORG$INVITATION_EMAIL_MISMATCH_TITLE");
    expect(modal).toHaveTextContent("ORG$INVITATION_EMAIL_MISMATCH_BODY");
    expect(modal).not.toHaveTextContent("NO_EMAIL");
  });

  it("explains the mismatch without an email when the account has none", () => {
    // Arrange
    mockMe.data = {};

    // Act
    renderModal();

    // Assert
    expect(
      screen.getByTestId("invitation-email-mismatch-modal"),
    ).toHaveTextContent("ORG$INVITATION_EMAIL_MISMATCH_BODY_NO_EMAIL");
  });

  it("signs out and keeps the invitation for the next sign-in", async () => {
    // Arrange
    const user = userEvent.setup();
    renderModal();

    // Act
    await user.click(screen.getByTestId("invitation-email-mismatch-sign-in"));

    // Assert
    expect(mockLogout).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(INVITATION_TOKEN_KEY)).toBe("inv-token");
  });

  it("discards the invitation when closed", async () => {
    // Arrange
    const user = userEvent.setup();
    renderModal();

    // Act
    await user.click(screen.getByTestId("invitation-email-mismatch-close"));

    // Assert
    expect(useInvitationEmailMismatchStore.getState().isOpen).toBe(false);
    expect(localStorage.getItem(INVITATION_TOKEN_KEY)).toBeNull();
    expect(mockLogout).not.toHaveBeenCalled();
  });
});
