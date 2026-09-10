import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "#/routes/login";
import AccountActionPage from "#/routes/account-action";
import ManageAccountsPage from "#/routes/manage-accounts";
import UserSettingsScreen from "#/routes/user-settings";
import AuthService from "#/api/auth-service/auth-service.api";
import { AuthCapabilities } from "#/api/auth-service/auth.types";
import { isValidNewPassword } from "#/utils/password-policy";

const { capabilitiesMock, meMock, logoutMock } = vi.hoisted(() => ({
  capabilitiesMock: vi.fn(),
  meMock: vi.fn(),
  logoutMock: vi.fn(),
}));
vi.mock("#/hooks/query/use-auth-capabilities", () => ({
  useAuthCapabilities: () => capabilitiesMock(),
}));
vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({ data: { app_mode: "saas" }, isLoading: false }),
}));
vi.mock("#/hooks/query/use-is-authed", () => ({
  useIsAuthed: () => ({ data: false, isLoading: false }),
}));
vi.mock("#/hooks/query/use-me", () => ({ useMe: () => meMock() }));
vi.mock("#/hooks/query/use-settings", () => ({
  useSettings: () => ({
    data: { email: "admin@example.com", email_verified: false },
    isLoading: false,
  }),
}));
vi.mock("#/hooks/mutation/use-logout", () => ({
  useLogout: () => ({ mutate: logoutMock }),
}));
vi.mock("#/hooks/use-email-verification", () => ({
  useEmailVerification: () => ({}),
}));

const localCapabilities: AuthCapabilities = {
  mode: "local",
  password_login: true,
  login_providers: [],
  registration: "admin_or_invitation",
  email_recovery: true,
  repository_connections: { manual_tokens: true, broker: false },
};
const Router = createRoutesStub([
  { path: "/login", Component: LoginPage },
  { path: "/auth/:action", Component: AccountActionPage },
  { path: "/settings/accounts", Component: ManageAccountsPage },
  { path: "/settings/user", Component: UserSettingsScreen },
]);
const deviceReturn = "/oauth/device/verify?user_code=AAAA-BBBB";
const password = " a new long password ";

function renderPage(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Router initialEntries={[path]} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.stubGlobal("location", {
    href: "https://app.example.com/login",
    origin: "https://app.example.com",
    pathname: "/login",
    search: "",
  });
  capabilitiesMock.mockReturnValue({
    data: localCapabilities,
    isLoading: false,
  });
  meMock.mockReturnValue({
    data: { permissions: ["manage_users"] },
    isLoading: false,
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("local password login", () => {
  it("uses password login despite a remembered provider and preserves invitation/device return", async () => {
    localStorage.setItem("login_method", "github");
    const login = vi
      .spyOn(AuthService, "login")
      .mockResolvedValue({
        password_change_required: true,
        redirect_url: "/auth/change-password?returnTo=device",
      });
    renderPage(
      `/login?returnTo=${encodeURIComponent(deviceReturn)}&invitation_token=invite-token`,
    );
    expect(
      screen.queryByText("GITHUB$CONNECT_TO_GITHUB"),
    ).not.toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText("SETTINGS$USER_EMAIL"),
      "admin@example.com",
    );
    await userEvent.type(screen.getByLabelText("AUTH$PASSWORD"), password);
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$PASSWORD_SIGN_IN" }),
    );
    await waitFor(() =>
      expect(login).toHaveBeenCalledWith(
        {
          email: "admin@example.com",
          password,
          redirect_url: deviceReturn,
          invitation_token: "invite-token",
        },
        expect.anything(),
      ),
    );
    await waitFor(() =>
      expect(window.location.href).toBe(
        "/auth/change-password?returnTo=device",
      ),
    );
  });

  it("hides email recovery when SMTP is unavailable but keeps password login", () => {
    capabilitiesMock.mockReturnValue({
      data: { ...localCapabilities, email_recovery: false },
    });
    renderPage("/login");
    expect(screen.getByLabelText("AUTH$PASSWORD")).toBeInTheDocument();
    expect(screen.queryByText("AUTH$FORGOT_PASSWORD")).not.toBeInTheDocument();
    expect(screen.getByText("AUTH$ENROLLMENT_POLICY")).toBeInTheDocument();
  });

  it("shows capability failure without offering OSS or provider fallback", () => {
    capabilitiesMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    renderPage("/login");
    expect(screen.getByRole("alert")).toHaveTextContent("AUTH$UNAVAILABLE");
    expect(screen.queryByLabelText("AUTH$PASSWORD")).not.toBeInTheDocument();
  });

  it("shows invalid credentials without losing the form or destination", async () => {
    vi.spyOn(AuthService, "login").mockRejectedValue({
      isAxiosError: true,
      response: { status: 401 },
    });
    renderPage("/login");
    await userEvent.type(
      screen.getByLabelText("SETTINGS$USER_EMAIL"),
      "user@example.com",
    );
    await userEvent.type(screen.getByLabelText("AUTH$PASSWORD"), "wrong");
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$PASSWORD_SIGN_IN" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "AUTH$INVALID_CREDENTIALS",
    );
    expect(screen.getByLabelText("SETTINGS$USER_EMAIL")).toHaveValue(
      "user@example.com",
    );
  });
});

describe("account lifecycle screens", () => {
  it("changes a temporary password without SMTP and obeys the server terms redirect", async () => {
    capabilitiesMock.mockReturnValue({
      data: { ...localCapabilities, email_recovery: false },
    });
    const change = vi
      .spyOn(AuthService, "changePassword")
      .mockResolvedValue({
        password_change_required: false,
        redirect_url: "/accept-tos?redirect_url=resume",
      });
    renderPage(
      `/auth/change-password?returnTo=${encodeURIComponent(deviceReturn)}`,
    );
    await userEvent.type(
      screen.getByLabelText("AUTH$CURRENT_PASSWORD"),
      "temporary password",
    );
    await userEvent.type(screen.getByLabelText("AUTH$NEW_PASSWORD"), password);
    await userEvent.type(
      screen.getByLabelText("AUTH$CONFIRM_PASSWORD"),
      password,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$CHANGE_PASSWORD" }),
    );
    await waitFor(() =>
      expect(change).toHaveBeenCalledWith(
        {
          current_password: "temporary password",
          new_password: password,
          redirect_url: deviceReturn,
          invitation_token: undefined,
        },
        expect.anything(),
      ),
    );
    await waitFor(() =>
      expect(window.location.href).toBe("/accept-tos?redirect_url=resume"),
    );
  });

  it("rejects mismatched passwords before sending a change", async () => {
    const change = vi.spyOn(AuthService, "changePassword");
    renderPage("/auth/change-password");
    await userEvent.type(
      screen.getByLabelText("AUTH$CURRENT_PASSWORD"),
      "temporary password",
    );
    await userEvent.type(screen.getByLabelText("AUTH$NEW_PASSWORD"), password);
    await userEvent.type(
      screen.getByLabelText("AUTH$CONFIRM_PASSWORD"),
      "different password",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$CHANGE_PASSWORD" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "AUTH$PASSWORD_MISMATCH",
    );
    expect(change).not.toHaveBeenCalled();
  });

  it("allows logout from a restricted password-change session", async () => {
    renderPage("/auth/change-password");
    await userEvent.click(
      screen.getByRole("button", { name: "ACCOUNT_SETTINGS$LOGOUT" }),
    );
    expect(logoutMock).toHaveBeenCalled();
  });

  it("requests recovery with a generic response and preserved return context", async () => {
    const forgot = vi
      .spyOn(AuthService, "forgotPassword")
      .mockResolvedValue(undefined);
    renderPage(
      `/auth/forgot-password?returnTo=${encodeURIComponent(deviceReturn)}`,
    );
    await userEvent.type(
      screen.getByLabelText("SETTINGS$USER_EMAIL"),
      "user@example.com",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$SEND_EMAIL" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "AUTH$CHECK_EMAIL",
    );
    expect(forgot).toHaveBeenCalledWith(
      {
        email: "user@example.com",
        redirect_url: deviceReturn,
        invitation_token: undefined,
      },
      expect.anything(),
    );
  });

  it("does not submit email recovery when unavailable", () => {
    capabilitiesMock.mockReturnValue({
      data: { ...localCapabilities, email_recovery: false },
    });
    renderPage("/auth/forgot-password");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "AUTH$EMAIL_UNAVAILABLE",
    );
    expect(
      screen.queryByRole("button", { name: "AUTH$SEND_EMAIL" }),
    ).not.toBeInTheDocument();
  });

  it("resets a password using its action token then returns to login", async () => {
    const reset = vi
      .spyOn(AuthService, "resetPassword")
      .mockResolvedValue({ redirect_url: "/login?returnTo=resume" });
    renderPage(
      "/auth/reset-password?returnTo=%2Fsettings%2Fuser#token=reset-token",
    );
    await userEvent.type(screen.getByLabelText("AUTH$NEW_PASSWORD"), password);
    await userEvent.type(
      screen.getByLabelText("AUTH$CONFIRM_PASSWORD"),
      password,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$RESET_PASSWORD" }),
    );
    await waitFor(() =>
      expect(reset).toHaveBeenCalledWith(
        {
          token: "reset-token",
          new_password: password,
          redirect_url: "/settings/user",
          invitation_token: undefined,
        },
        expect.anything(),
      ),
    );
    expect(window.location.href).toBe("/login?returnTo=resume");
  });

  it("requires a token for resetting a password", () => {
    renderPage("/auth/reset-password?token=untrusted-query-token");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "AUTH$INVALID_ACTION_LINK",
    );
    expect(
      screen.queryByLabelText("AUTH$NEW_PASSWORD"),
    ).not.toBeInTheDocument();
  });

  it("verifies an email only when the user confirms and preserves an invitation", async () => {
    const verify = vi
      .spyOn(AuthService, "verifyEmail")
      .mockResolvedValue({ redirect_url: "/onboarding?returnTo=resume" });
    renderPage(
      `/auth/verify-email?invitation_token=invite-token&returnTo=${encodeURIComponent(deviceReturn)}#token=verify-token`,
    );
    expect(verify).not.toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$VERIFY_EMAIL" }),
    );
    await waitFor(() =>
      expect(verify).toHaveBeenCalledWith(
        {
          token: "verify-token",
          invitation_token: "invite-token",
          redirect_url: deviceReturn,
        },
        expect.anything(),
      ),
    );
    expect(window.location.href).toBe("/onboarding?returnTo=resume");
  });

  it("enrolls an invited account without offering public registration", async () => {
    const enroll = vi
      .spyOn(AuthService, "enroll")
      .mockResolvedValue({
        redirect_url: "/accept-tos",
        password_change_required: false,
      });
    renderPage(
      `/auth/enroll?invitation_token=invite-token&returnTo=${encodeURIComponent(deviceReturn)}`,
    );
    await userEvent.type(screen.getByLabelText("AUTH$NEW_PASSWORD"), password);
    await userEvent.type(
      screen.getByLabelText("AUTH$CONFIRM_PASSWORD"),
      password,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$ENROLL_INVITATION" }),
    );
    await waitFor(() =>
      expect(enroll).toHaveBeenCalledWith(
        {
          invitation_token: "invite-token",
          password,
          redirect_url: deviceReturn,
        },
        expect.anything(),
      ),
    );
  });

  it("does not enroll an account without an invitation", () => {
    renderPage("/auth/enroll");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "AUTH$INVALID_ACTION_LINK",
    );
    expect(
      screen.queryByLabelText("AUTH$NEW_PASSWORD"),
    ).not.toBeInTheDocument();
  });

  it("shows an expired action token error", async () => {
    vi.spyOn(AuthService, "verifyEmail").mockRejectedValue({
      isAxiosError: true,
      response: { status: 400, data: { detail: { code: "invalid_token" } } },
    });
    renderPage("/auth/verify-email#token=expired-token");
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$VERIFY_EMAIL" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "AUTH$INVALID_ACTION_LINK",
    );
  });
});

describe("local account administration and settings", () => {
  it("creates an account through the lifecycle API and clears the temporary password", async () => {
    const create = vi
      .spyOn(AuthService, "createAccount")
      .mockResolvedValue({
        id: "new-user",
        email: "new@example.com",
        password_change_required: true,
      });
    renderPage("/settings/accounts");
    await userEvent.type(
      screen.getByLabelText("SETTINGS$USER_EMAIL"),
      "new@example.com",
    );
    await userEvent.type(
      screen.getByLabelText("AUTH$INITIAL_PASSWORD"),
      password,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$CREATE_ACCOUNT" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "AUTH$ACCOUNT_CREATED",
    );
    expect(create).toHaveBeenCalledWith(
      { email: "new@example.com", initial_password: password },
      expect.anything(),
    );
    expect(screen.getByLabelText("AUTH$INITIAL_PASSWORD")).toHaveValue("");
  });

  it("does not expose account creation for organization-only administrators", () => {
    meMock.mockReturnValue({
      data: { role: "admin", permissions: ["invite_user_to_organization"] },
    });
    renderPage("/settings/accounts");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "AUTH$ACCOUNT_ACCESS_DENIED",
    );
    expect(
      screen.queryByLabelText("AUTH$INITIAL_PASSWORD"),
    ).not.toBeInTheDocument();
  });

  it("verifies replacement email before changing the displayed sign-in email", async () => {
    const change = vi
      .spyOn(AuthService, "changeEmail")
      .mockResolvedValue(undefined);
    renderPage("/settings/user");
    await userEvent.type(
      screen.getByLabelText("AUTH$NEW_LOGIN_EMAIL"),
      "replacement@example.com",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "AUTH$SEND_EMAIL" }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "AUTH$CHECK_EMAIL",
    );
    expect(change).toHaveBeenCalledWith(
      {
        email: "replacement@example.com",
        redirect_url: "/settings/user",
        invitation_token: undefined,
      },
      expect.anything(),
    );
    expect(screen.getByText("admin@example.com")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "AUTH$CHANGE_PASSWORD" }),
    ).toHaveAttribute(
      "href",
      "/auth/change-password?returnTo=%2Fsettings%2Fuser",
    );
  });
});

it("counts password length in Unicode characters and permits whitespace", () => {
  expect(isValidNewPassword("🔑".repeat(14))).toBe(false);
  expect(isValidNewPassword("🔑".repeat(15))).toBe(true);
  expect(isValidNewPassword(" ".repeat(15))).toBe(true);
  expect(isValidNewPassword("x".repeat(1025))).toBe(false);
});
