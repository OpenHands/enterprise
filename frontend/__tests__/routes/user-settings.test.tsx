import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UserSettingsScreen from "#/routes/user-settings";

const {
  useConfigMock,
  useHasPasswordMock,
  useSetPasswordMock,
  setPasswordMutateMock,
} = vi.hoisted(() => ({
  useConfigMock: vi.fn(),
  useHasPasswordMock: vi.fn(),
  useSetPasswordMock: vi.fn(),
  setPasswordMutateMock: vi.fn(),
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: useConfigMock,
}));

vi.mock("#/hooks/query/use-has-password", () => ({
  useHasPassword: useHasPasswordMock,
}));

vi.mock("#/hooks/mutation/use-set-password", () => ({
  useSetPassword: useSetPasswordMock,
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
    useHasPasswordMock.mockReset();
    useSetPasswordMock.mockReset();
    setPasswordMutateMock.mockReset();
    useHasPasswordMock.mockReturnValue({
      data: { has_password: false },
      isLoading: false,
    });
    useSetPasswordMock.mockReturnValue({
      mutate: setPasswordMutateMock,
      isPending: false,
    });
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
    expect(
      screen.queryByTestId("email-change-disabled"),
    ).not.toBeInTheDocument();
  });
});

describe("UserSettingsScreen set password section", () => {
  beforeEach(() => {
    useConfigMock.mockReset();
    useHasPasswordMock.mockReset();
    useSetPasswordMock.mockReset();
    setPasswordMutateMock.mockReset();
    useSetPasswordMock.mockReturnValue({
      mutate: setPasswordMutateMock,
      isPending: false,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("is hidden when enable_integrated_idp is off", () => {
    useConfigMock.mockReturnValue({
      data: { feature_flags: { enable_integrated_idp: false } },
    });
    useHasPasswordMock.mockReturnValue({
      data: { has_password: false },
      isLoading: false,
    });

    renderScreen();

    expect(
      screen.queryByTestId("set-password-section"),
    ).not.toBeInTheDocument();
  });

  it("hides the current-password field when no password is set yet", async () => {
    useConfigMock.mockReturnValue({
      data: { feature_flags: { enable_integrated_idp: true } },
    });
    useHasPasswordMock.mockReturnValue({
      data: { has_password: false },
      isLoading: false,
    });

    renderScreen();

    expect(
      await screen.findByTestId("set-password-section"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("current-password-input"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("new-password-input")).toBeInTheDocument();
    expect(
      screen.getByTestId("confirm-new-password-input"),
    ).toBeInTheDocument();
  });

  it("shows the current-password field when a password already exists", async () => {
    useConfigMock.mockReturnValue({
      data: { feature_flags: { enable_integrated_idp: true } },
    });
    useHasPasswordMock.mockReturnValue({
      data: { has_password: true },
      isLoading: false,
    });

    renderScreen();

    expect(
      await screen.findByTestId("current-password-input"),
    ).toBeInTheDocument();
  });

  it("blocks submission and shows an error when passwords do not match", async () => {
    useConfigMock.mockReturnValue({
      data: { feature_flags: { enable_integrated_idp: true } },
    });
    useHasPasswordMock.mockReturnValue({
      data: { has_password: false },
      isLoading: false,
    });

    renderScreen();

    fireEvent.change(await screen.findByTestId("new-password-input"), {
      target: { value: "password123" },
    });
    fireEvent.change(screen.getByTestId("confirm-new-password-input"), {
      target: { value: "different123" },
    });
    fireEvent.click(screen.getByTestId("set-password-button"));

    expect(
      await screen.findByTestId("confirm-new-password-input-error"),
    ).toBeInTheDocument();
    expect(setPasswordMutateMock).not.toHaveBeenCalled();
  });

  it("blocks submission when the new password is too short", async () => {
    useConfigMock.mockReturnValue({
      data: { feature_flags: { enable_integrated_idp: true } },
    });
    useHasPasswordMock.mockReturnValue({
      data: { has_password: false },
      isLoading: false,
    });

    renderScreen();

    fireEvent.change(await screen.findByTestId("new-password-input"), {
      target: { value: "short" },
    });
    fireEvent.change(screen.getByTestId("confirm-new-password-input"), {
      target: { value: "short" },
    });
    fireEvent.click(screen.getByTestId("set-password-button"));

    expect(
      await screen.findByTestId("new-password-input-error"),
    ).toBeInTheDocument();
    expect(setPasswordMutateMock).not.toHaveBeenCalled();
  });

  it("blocks submission when a required current password is missing", async () => {
    useConfigMock.mockReturnValue({
      data: { feature_flags: { enable_integrated_idp: true } },
    });
    useHasPasswordMock.mockReturnValue({
      data: { has_password: true },
      isLoading: false,
    });

    renderScreen();

    fireEvent.change(await screen.findByTestId("new-password-input"), {
      target: { value: "password123" },
    });
    fireEvent.change(screen.getByTestId("confirm-new-password-input"), {
      target: { value: "password123" },
    });
    fireEvent.click(screen.getByTestId("set-password-button"));

    expect(
      await screen.findByTestId("current-password-input-error"),
    ).toBeInTheDocument();
    expect(setPasswordMutateMock).not.toHaveBeenCalled();
  });

  it("submits current and new password when valid", async () => {
    useConfigMock.mockReturnValue({
      data: { feature_flags: { enable_integrated_idp: true } },
    });
    useHasPasswordMock.mockReturnValue({
      data: { has_password: true },
      isLoading: false,
    });

    renderScreen();

    fireEvent.change(await screen.findByTestId("current-password-input"), {
      target: { value: "old-password" },
    });
    fireEvent.change(screen.getByTestId("new-password-input"), {
      target: { value: "new-password123" },
    });
    fireEvent.change(screen.getByTestId("confirm-new-password-input"), {
      target: { value: "new-password123" },
    });
    fireEvent.click(screen.getByTestId("set-password-button"));

    expect(setPasswordMutateMock).toHaveBeenCalledWith(
      {
        currentPassword: "old-password",
        newPassword: "new-password123",
        confirmPassword: "new-password123",
      },
      expect.objectContaining({
        onSuccess: expect.any(Function),
        onError: expect.any(Function),
      }),
    );
  });
});
