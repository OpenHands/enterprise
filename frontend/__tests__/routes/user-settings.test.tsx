import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UserSettingsScreen from "#/routes/user-settings";

const { useConfigMock } = vi.hoisted(() => ({
  useConfigMock: vi.fn(),
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: useConfigMock,
}));

vi.mock("#/hooks/query/use-settings", () => ({
  useSettings: () => ({
    data: {
      email: "engineer@example.com",
      email_verified: true,
    },
    isLoading: false,
    refetch: vi.fn(),
  }),
}));

vi.mock("#/context/use-selected-organization", () => ({
  useSelectedOrganizationId: () => ({ organizationId: "org-1" }),
}));

vi.mock("#/hooks/use-email-verification", () => ({
  useEmailVerification: () => ({
    resendEmailVerification: vi.fn(),
    isResendingVerification: false,
  }),
}));

vi.mock("react-i18next", async () => {
  const actual =
    await vi.importActual<typeof import("react-i18next")>("react-i18next");
  return {
    ...actual,
    useTranslation: () => ({
      t: (key: string) =>
        key === "SETTINGS$EMAIL_CHANGE_DISABLED"
          ? "Email changes are disabled for this deployment."
          : key,
    }),
  };
});

const renderScreen = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <UserSettingsScreen />
    </QueryClientProvider>,
  );
};

describe("UserSettingsScreen email editing", () => {
  beforeEach(() => {
    useConfigMock.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows the email read-only when changes are disabled", async () => {
    useConfigMock.mockReturnValue({
      data: { email_change_enabled: false },
    });

    renderScreen();

    const input = await screen.findByTestId("email-input");
    expect(input).toHaveValue("engineer@example.com");
    expect(input).toHaveProperty("readOnly", true);
    expect(screen.queryByTestId("save-email-button")).not.toBeInTheDocument();
    expect(screen.getByTestId("email-change-disabled")).toHaveTextContent(
      "Email changes are disabled for this deployment.",
    );
  });

  it("keeps email editing enabled by default", async () => {
    useConfigMock.mockReturnValue({ data: {} });

    renderScreen();

    const input = await screen.findByTestId("email-input");
    expect(input).toHaveProperty("readOnly", false);
    expect(screen.getByTestId("save-email-button")).toBeInTheDocument();
    expect(screen.queryByTestId("email-change-disabled")).not.toBeInTheDocument();
  });
});
