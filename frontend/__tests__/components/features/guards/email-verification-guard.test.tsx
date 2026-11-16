import { render } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EmailVerificationGuard } from "#/components/features/guards/email-verification-guard";

const mockNavigate = vi.fn();

vi.mock("react-router", async (importOriginal) => {
  const original = await importOriginal<typeof import("react-router")>();
  return {
    ...original,
    useNavigate: () => mockNavigate,
  };
});

const mockUseConfig = vi.fn();
const mockUseSettings = vi.fn();

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => mockUseConfig(),
}));

vi.mock("#/hooks/query/use-settings", () => ({
  useSettings: () => mockUseSettings(),
}));

const renderGuardAt = (initialEntry: string) => {
  const Stub = createRoutesStub([
    {
      path: "*",
      Component: () => (
        <EmailVerificationGuard>
          <div data-testid="children" />
        </EmailVerificationGuard>
      ),
    },
  ]);

  return render(<Stub initialEntries={[initialEntry]} />);
};

const setup = ({
  requireVerifiedEmails,
  emailVerified,
}: {
  requireVerifiedEmails?: boolean;
  emailVerified?: boolean;
}) => {
  mockUseConfig.mockReturnValue({
    data: { require_verified_emails: requireVerifiedEmails },
  });
  mockUseSettings.mockReturnValue({
    data: { email_verified: emailVerified },
    isLoading: false,
  });
};

describe("EmailVerificationGuard", () => {
  beforeEach(() => {
    mockNavigate.mockReset();
  });

  it("redirects an unverified user to /settings/user when verified emails are required", async () => {
    setup({ requireVerifiedEmails: true, emailVerified: false });

    renderGuardAt("/conversations");

    await vi.waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/settings/user", {
        replace: true,
      });
    });
  });

  it("does not redirect from /settings/user itself", () => {
    setup({ requireVerifiedEmails: true, emailVerified: false });

    renderGuardAt("/settings/user");

    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it.each([false, undefined])(
    "does not redirect an unverified user when require_verified_emails is %s",
    (requireVerifiedEmails) => {
      setup({ requireVerifiedEmails, emailVerified: false });

      renderGuardAt("/conversations");

      expect(mockNavigate).not.toHaveBeenCalled();
    },
  );

  it("does not redirect a verified user", () => {
    setup({ requireVerifiedEmails: true, emailVerified: true });

    renderGuardAt("/conversations");

    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
