import { containingForm } from "../helpers/native-fixtures";
import { WebClientConfig } from "#/api/option-service/option.types";
import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { AxiosError, AxiosHeaders } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import LoginPage from "#/routes/login";
import AccountSetup from "#/routes/account-setup";
import SamlComplete from "#/routes/saml-complete";
import AdminUsers from "#/routes/admin-users";
import { AccountSignInMethods } from "#/components/features/native-auth/account-sign-in-methods";
import { PasswordChange } from "#/components/features/native-auth/password-change";
import { NativeAuthError, NativeAuthService, NativeAccount } from "#/api/native-auth-service/native-auth-service.api";
import AuthService from "#/api/auth-service/auth-service.api";
import { openHands } from "#/api/open-hands-axios";

interface MockAuthState { config: Pick<WebClientConfig, "auth_mode" | "app_mode" | "login_methods">; authenticated: boolean }
const state = vi.hoisted((): MockAuthState => ({
  config: { auth_mode: "native", app_mode: "saas", login_methods: ["password", "saml"] },
  authenticated: false,
}));
vi.mock("#/hooks/query/use-config", () => ({ useConfig: () => ({ data: state.config, isLoading: false }) }));
vi.mock("#/hooks/query/use-is-authed", () => ({ useIsAuthed: () => ({ data: state.authenticated, acceptedTos: false }) }));

function Destination(): React.JSX.Element {
  const location = useLocation();
  return <p data-testid="destination">{location.pathname + location.search}</p>;
}

function mount(Component: () => React.ReactNode, entry: string = "/login", client: QueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })): QueryClient {
  const path = entry.split("?")[0];
  const Routes = createRoutesStub([
    { path, Component },
    { path: "/accept-tos", Component: Destination },
    { path: "/conversations/task", Component: Destination },
    { path: "/", Component: Destination },
  ]);
  render(<StrictMode><QueryClientProvider client={client}><Routes initialEntries={[entry]} /></QueryClientProvider></StrictMode>);
  return client;
}

const cachedState = (client: QueryClient): string => JSON.stringify({
  queries: client.getQueryCache().getAll().map((query) => query.state),
  mutations: client.getMutationCache().getAll().map((mutation) => mutation.state),
});

describe("native SAML sign-in", () => {
  const assign = vi.fn();
  beforeEach(() => {
    state.config.login_methods = ["password", "saml"];
    state.authenticated = false;
    localStorage.clear();
    sessionStorage.clear();
    assign.mockClear();
    const location = window.location;
    vi.stubGlobal("location", {
      get pathname(): string { return location.pathname; },
      get search(): string { return location.search; },
      get hash(): string { return location.hash; },
      get origin(): string { return location.origin; },
      get href(): string { return location.href; },
      assign,
      replace: vi.fn(),
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  it.each<WebClientConfig["login_methods"]>([undefined, ["password"]])("retains the password form when SAML is absent (%s)", (methods) => {
    state.config.login_methods = methods;
    mount(LoginPage);
    expect(screen.getByLabelText("NATIVE_AUTH$EMAIL")).toBeInTheDocument();
    expect(screen.getByLabelText("NATIVE_AUTH$PASSWORD")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /GITHUB|GITLAB|BITBUCKET/ })).not.toBeInTheDocument();
  });

  it("offers both choices and signs in with email while preserving the deep link", async () => {
    const login = vi.spyOn(NativeAuthService, "login").mockResolvedValue({ redirect_to: "/conversations/task?tab=terminal", accepted_tos: true });
    const client = mount(LoginPage, "/login?returnTo=%2Fconversations%2Ftask%3Ftab%3Dterminal&login_method=github");
    client.setQueryData(["old-account"], { id: "previous-user" });
    expect(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" })).toBeInTheDocument();
    expect(screen.queryByLabelText("NATIVE_AUTH$PASSWORD")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_EMAIL" }));
    fireEvent.change(screen.getByLabelText("NATIVE_AUTH$EMAIL"), { target: { value: "email@example.com" } });
    fireEvent.change(screen.getByLabelText("NATIVE_AUTH$PASSWORD"), { target: { value: "private-password" } });
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN" }));
    expect(await screen.findByTestId("destination")).toHaveTextContent("/conversations/task?tab=terminal");
    expect(login).toHaveBeenCalledWith(expect.objectContaining({ email: "email@example.com", password: "private-password", return_path: "/conversations/task?tab=terminal" }));
    expect(client.getQueryData(["old-account"])).toBeUndefined();
    expect(cachedState(client)).not.toContain("private-password");
  });

  it.each(["https://evil.example/path", "//evil.example/path", "/\\evil.example/path", "/\ninvalid"])("sanitizes the requested SSO return path %s", async (returnTo) => {
    const start = vi.spyOn(NativeAuthService, "startSaml").mockResolvedValue({ redirect_to: "https://idp.example/sso?SAMLRequest=private-request" });
    const client = mount(LoginPage, `/login?returnTo=${encodeURIComponent(returnTo)}`);
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" }));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://idp.example/sso?SAMLRequest=private-request"));
    expect(start).toHaveBeenCalledWith({ return_path: "/" });
    expect(cachedState(client)).not.toContain("private-request");
  });

  it("uses the CSRF-aware DAL to start SSO with only a local return path", async () => {
    const post = vi.spyOn(openHands, "post").mockResolvedValue({ data: { redirect_to: "https://idp.example/sso" } });
    mount(LoginPage, "/login?returnTo=%2Fconversations%2Ftask%3Ftab%3Dterminal");
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" }));
    await waitFor(() => expect(assign).toHaveBeenCalledOnce());
    expect(post).toHaveBeenCalledWith("/api/auth/saml/start", { return_path: "/conversations/task?tab=terminal" });
  });

  it("shows a generic retryable SSO error without reflecting backend or callback data", async () => {
    const config = { headers: new AxiosHeaders(), data: { invitation_token: "secret-invitation" } };
    vi.spyOn(openHands, "post").mockRejectedValue(new AxiosError("secret-response", "400", config, null, { config, data: { detail: "secret-assertion" }, status: 400, statusText: "", headers: {} }));
    const client = mount(LoginPage, "/login?sso_error=secret-callback");
    expect(screen.getByRole("alert")).toHaveTextContent("NATIVE_AUTH$SSO_FAILED");
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" }));
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
    expect(assign).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toMatch(/secret-/);
    expect(cachedState(client)).not.toMatch(/secret-/);
    expect(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" })).toBeEnabled();
  });

  it.each(["set_password", "login"] as const)("sends an invitation only in the SSO start body for %s", async (action) => {
    window.history.replaceState({}, "", "/account-setup#token=private-invitation");
    const inspect = vi.spyOn(NativeAuthService, "inspect").mockResolvedValue({ email: "invited@example.com", org_id: "team", org_name: "Team", org_role_id: 3, expires_at: "2099-01-01", action, ...(action === "login" ? { authentication_methods: ["saml"] } : {}) });
    const post = vi.spyOn(openHands, "post").mockResolvedValue({ data: { redirect_to: "https://idp.example/sso?SAMLRequest=secret-request" } });
    const client = mount(AccountSetup, "/account-setup");
    expect(window.location.hash).toBe("");
    fireEvent.click(await screen.findByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" }));
    await waitFor(() => expect(assign).toHaveBeenCalledOnce());
    expect(inspect).toHaveBeenCalledOnce();
    expect(post).toHaveBeenCalledWith("/api/auth/saml/start", { return_path: "/", invitation_token: "private-invitation" });
    if (action === "login") expect(screen.queryByLabelText("NATIVE_AUTH$PASSWORD")).not.toBeInTheDocument();
    expect(cachedState(client)).not.toMatch(/private-invitation|secret-request/);
    expect(JSON.stringify(localStorage)).not.toContain("private-invitation");
    expect(JSON.stringify(sessionStorage)).not.toContain("private-invitation");
  });

  it("completes once under StrictMode, restores terms and replaces prior account data", async () => {
    const post = vi.spyOn(openHands, "post").mockResolvedValue({ data: { redirect_to: "/accept-tos?redirect_url=%2Fconversations%2Ftask" } });
    const session = vi.spyOn(AuthService, "nativeSession").mockResolvedValue({ accepted_tos: false });
    const client = new QueryClient();
    client.setQueryData(["old-account"], { id: "previous-user" });
    client.setQueryData(["web-client-config"], state.config);
    mount(SamlComplete, "/auth/saml/complete", client);
    expect(await screen.findByTestId("destination")).toHaveTextContent("/accept-tos?redirect_url=%2Fconversations%2Ftask");
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/auth/saml/complete", {});
    expect(session).toHaveBeenCalledOnce();
    expect(client.getQueryData(["old-account"])).toBeUndefined();
    expect(client.getQueryData(["web-client-config"])).toEqual(state.config);
    expect(client.getQueryData(["user", "authenticated", "saas", "native"])).toEqual({ authenticated: true, acceptedTos: false });
  });

  it("refreshes sign-in methods when another enrollment creates an SSO-only account", async () => {
    window.history.replaceState({}, "", "/account-setup#token=concurrent-invitation");
    const invitation = { email: "invited@example.com", org_id: null, org_name: null, org_role_id: null, expires_at: "2099-01-01" };
    vi.spyOn(NativeAuthService, "inspect")
      .mockResolvedValueOnce({ ...invitation, action: "set_password", authentication_methods: [] })
      .mockResolvedValueOnce({ ...invitation, action: "login", authentication_methods: ["saml"] });
    vi.spyOn(NativeAuthService, "enroll").mockResolvedValue({ action: "login" });
    mount(AccountSetup, "/account-setup");
    fireEvent.change(await screen.findByLabelText("NATIVE_AUTH$NEW_PASSWORD"), { target: { value: "proposed long password" } });
    fireEvent.change(screen.getByLabelText("NATIVE_AUTH$CONFIRM_PASSWORD"), { target: { value: "proposed long password" } });
    fireEvent.submit(containingForm(screen.getByRole("button", { name: "NATIVE_AUTH$SET_UP_ACCOUNT" })));
    await screen.findByText("NATIVE_AUTH$EXISTING_ACCOUNT");
    expect(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" })).toBeInTheDocument();
    expect(screen.queryByLabelText("NATIVE_AUTH$PASSWORD")).not.toBeInTheDocument();
    expect(screen.queryByText("NATIVE_AUTH$NO_LOGIN_METHODS")).not.toBeInTheDocument();
  });

  it("does not follow an external completion destination", async () => {
    vi.spyOn(NativeAuthService, "completeSaml").mockResolvedValue({ redirect_to: "https://evil.example", accepted_tos: true });
    mount(SamlComplete, "/auth/saml/complete");
    expect(await screen.findByTestId("destination")).toHaveTextContent("/");
    expect(assign).not.toHaveBeenCalled();
  });

  it("does not retry expired completion and offers a fresh login", async () => {
    const complete = vi.spyOn(NativeAuthService, "completeSaml").mockRejectedValue(new Error("private-response"));
    mount(SamlComplete, "/auth/saml/complete");
    expect(await screen.findByRole("alert")).toHaveTextContent("NATIVE_AUTH$SSO_FAILED");
    expect(screen.getByRole("link", { name: "NATIVE_AUTH$BACK_TO_LOGIN" })).toHaveAttribute("href", "/login");
    expect(document.body.textContent).not.toContain("private-response");
    expect(complete).toHaveBeenCalledOnce();
  });

  it.each([
    ["account_link_required", "NATIVE_AUTH$SSO_ACCOUNT_LINK_REQUIRED"],
    ["invitation_required", "NATIVE_AUTH$SSO_INVITATION_REQUIRED"],
    ["email_mismatch", "NATIVE_AUTH$SSO_EMAIL_MISMATCH"],
    ["unavailable", "NATIVE_AUTH$SSO_UNAVAILABLE"],
    ["recent_auth_required", "NATIVE_AUTH$SSO_RECENT_AUTH_REQUIRED"],
    ["temporarily_unavailable", "NATIVE_AUTH$SSO_TEMPORARILY_UNAVAILABLE"],
    ["invalid_response", "NATIVE_AUTH$SSO_FAILED"],
    ["secret-unrecognized-code", "NATIVE_AUTH$SSO_FAILED"],
  ])("shows fixed completion guidance for %s without retaining protocol details", async (code, message) => {
    const config = { headers: new AxiosHeaders(), data: { invitation_token: "secret-invitation" } };
    const post = vi.spyOn(openHands, "post").mockRejectedValue(new AxiosError("secret-response", "403", config, null, { config, data: { code, detail: "secret-assertion", email: "secret-email" }, status: 403, statusText: "", headers: {} }));
    const client = mount(SamlComplete, "/auth/saml/complete");
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/auth/saml/complete", {});
    expect(screen.getByRole("link", { name: "NATIVE_AUTH$BACK_TO_LOGIN" })).toHaveAttribute("href", "/login");
    expect(document.body.textContent).not.toMatch(/secret-/);
    expect(cachedState(client)).not.toMatch(/secret-/);
    expect(JSON.stringify(localStorage)).not.toMatch(/secret-/);
    expect(JSON.stringify(sessionStorage)).not.toMatch(/secret-/);
  });

  it.each([
    ["account_link_required", "NATIVE_AUTH$SSO_ACCOUNT_LINK_REQUIRED"],
    ["invitation_required", "NATIVE_AUTH$SSO_INVITATION_REQUIRED"],
    ["email_mismatch", "NATIVE_AUTH$SSO_EMAIL_MISMATCH"],
    ["unavailable", "NATIVE_AUTH$SSO_UNAVAILABLE"],
    ["recent_auth_required", "NATIVE_AUTH$SSO_RECENT_AUTH_REQUIRED"],
    ["temporarily_unavailable", "NATIVE_AUTH$SSO_TEMPORARILY_UNAVAILABLE"],
    ["invalid_response", "NATIVE_AUTH$SSO_FAILED"],
    ["secret-unrecognized-code", "NATIVE_AUTH$SSO_FAILED"],
  ])("shows fixed start guidance for %s and permits a new sign-in attempt", async (code, message) => {
    const config = { headers: new AxiosHeaders(), data: { SAMLResponse: "secret-response" } };
    vi.spyOn(openHands, "post").mockRejectedValue(new AxiosError("secret-response", "403", config, null, { config, data: { code, detail: "secret-assertion" }, status: 403, statusText: "", headers: {} }));
    const client = mount(LoginPage);
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" })).toBeEnabled();
    expect(document.body.textContent).not.toMatch(/secret-/);
    expect(cachedState(client)).not.toMatch(/secret-/);
    expect(assign).not.toHaveBeenCalled();
  });

  it("requests fresh SSO authentication for an administrator and leaves the action for an explicit retry", async () => {
    state.authenticated = true;
    const account: NativeAccount = { id: "member", email: "member@example.com", state: "profile_present", profile_present: true, is_disabled: false, role_id: null, created_at: "2026-01-01", pending_invitations: [], authentication_methods: ["password"] };
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "admin", email: "sso-admin@example.com", global_permissions: ["manage_users"], has_password: false, authentication_methods: ["saml"] });
    vi.spyOn(NativeAuthService, "invitationOrganizations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "accounts").mockResolvedValue({ items: [account], total: 1 });
    vi.spyOn(NativeAuthService, "account").mockResolvedValue(account);
    vi.spyOn(NativeAuthService, "invitations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "roles").mockResolvedValue([]);
    const issueReset = vi.spyOn(NativeAuthService, "issueReset").mockRejectedValue(new NativeAuthError(403, "Recent authentication required"));
    const post = vi.spyOn(openHands, "post").mockResolvedValue({ data: { redirect_to: "https://idp.example/sso?SAMLRequest=secret-reauth" } });
    const client = mount(AdminUsers, "/settings/users");
    fireEvent.click(await screen.findByRole("button", { name: "member@example.com" }));
    fireEvent.click(await screen.findByRole("button", { name: "NATIVE_AUTH$CREATE_RESET_LINK" }));
    fireEvent.click(await screen.findByRole("button", { name: "NATIVE_AUTH$SIGN_IN_SSO" }));
    await waitFor(() => expect(assign).toHaveBeenCalledOnce());
    expect(post).toHaveBeenCalledWith("/api/auth/saml/start", { return_path: "/settings/users", reauthenticate: true });
    expect(issueReset).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("NATIVE_AUTH$PASSWORD")).not.toBeInTheDocument();
    expect(cachedState(client)).not.toContain("secret-reauth");
  });

  it("does not call SAML completion if the backend does not advertise it", async () => {
    state.config.login_methods = ["password"];
    const complete = vi.spyOn(NativeAuthService, "completeSaml");
    mount(SamlComplete, "/auth/saml/complete");
    await screen.findByRole("alert");
    expect(complete).not.toHaveBeenCalled();
  });

  it("shows SSO profile information and hides password operations for a federated-only account", async () => {
    state.authenticated = true;
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "sso-account", email: "sso@example.com", global_permissions: [], has_password: false, authentication_methods: ["saml"] });
    mount(() => <><AccountSignInMethods /><PasswordChange /></>, "/settings/user");
    await screen.findByText("NATIVE_AUTH$SSO_METHOD");
    expect(screen.getByText("NATIVE_AUTH$SSO_PASSWORD_HELP")).toBeInTheDocument();
    expect(screen.queryByLabelText("NATIVE_AUTH$CURRENT_PASSWORD")).not.toBeInTheDocument();
    expect(screen.queryByText("NATIVE_AUTH$CHANGE_PASSWORD")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "NATIVE_AUTH$LINK_SSO" })).not.toBeInTheDocument();
  });

  it("requires existing-account password reauthentication before retrying explicit SSO linking", async () => {
    state.authenticated = true;
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "password-account", email: "existing@example.com", global_permissions: [], has_password: true, authentication_methods: ["password"] });
    const start = vi.spyOn(NativeAuthService, "startSaml").mockRejectedValueOnce(new NativeAuthError(403, "Recent authentication required")).mockResolvedValue({ redirect_to: "https://idp.example/sso" });
    const login = vi.spyOn(NativeAuthService, "login").mockResolvedValue({ redirect_to: "/", accepted_tos: true });
    const client = mount(AccountSignInMethods, "/settings/user");
    fireEvent.click(await screen.findByRole("button", { name: "NATIVE_AUTH$LINK_SSO" }));
    fireEvent.change(await screen.findByLabelText("NATIVE_AUTH$PASSWORD"), { target: { value: "private-reauth-password" } });
    expect(screen.getByLabelText("NATIVE_AUTH$EMAIL")).toHaveAttribute("readonly");
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$CONFIRM_IDENTITY" }));
    fireEvent.click(await screen.findByRole("button", { name: "NATIVE_AUTH$LINK_SSO" }));
    await waitFor(() => expect(assign).toHaveBeenCalledOnce());
    expect(start).toHaveBeenNthCalledWith(2, { return_path: "/settings/user", link: true });
    expect(login).toHaveBeenCalledWith(expect.objectContaining({ email: "existing@example.com", password: "private-reauth-password" }));
    expect(cachedState(client)).not.toContain("private-reauth-password");
  });

  it("does not request password confirmation for an unavailable SSO link", async () => {
    state.authenticated = true;
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "password-account", email: "existing@example.com", global_permissions: [], has_password: true, authentication_methods: ["password"] });
    vi.spyOn(NativeAuthService, "startSaml").mockRejectedValue(new NativeAuthError(403, "Static message", "unavailable"));
    mount(AccountSignInMethods, "/settings/user");
    fireEvent.click(await screen.findByRole("button", { name: "NATIVE_AUTH$LINK_SSO" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("NATIVE_AUTH$SSO_UNAVAILABLE");
    expect(screen.queryByLabelText("NATIVE_AUTH$PASSWORD")).not.toBeInTheDocument();
  });
});
