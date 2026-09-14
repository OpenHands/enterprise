import type { AxiosResponse } from "axios";
import { axiosResponse, containingForm } from "../helpers/native-fixtures";
import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AccountSetup from "#/routes/account-setup";
import PasswordReset from "#/routes/password-reset";
import { NativeAuthService } from "#/api/native-auth-service/native-auth-service.api";
import { openHands } from "#/api/open-hands-axios";
import { AccountLinkModal } from "#/components/features/admin-users/account-link-modal";

vi.mock("#/hooks/query/use-config", () => ({ useConfig: () => ({ data: { auth_mode: "native", app_mode: "saas" }, isLoading: false }) }));

function mount(path: string, component: typeof AccountSetup): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Routes = createRoutesStub([
    { path, Component: component },
    { path: "/accept-tos", Component: () => <p>TOS destination</p> },
    { path: "/login", Component: () => <p>Login destination</p> },
  ]);
  render(<StrictMode><QueryClientProvider client={client}><Routes initialEntries={[path]} /></QueryClientProvider></StrictMode>);
  return client;
}

describe("native account recipient forms", () => {
  beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });
  afterEach(() => { vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

  it("scrubs a fragment, retains it through password login, then accepts exact membership before TOS", async () => {
    window.history.replaceState({}, "", "/account-setup#token=private-setup-token");
    vi.spyOn(NativeAuthService, "inspect").mockResolvedValue({ email: "existing@example.com", org_name: "Team", org_id: "org", org_role_id: 3, expires_at: "2099-01-01", action: "login" });
    const enroll = vi.spyOn(NativeAuthService, "enroll");
    const requests: string[] = [];
    vi.spyOn(openHands, "post").mockImplementation(async (path: string, payload?: unknown): Promise<AxiosResponse<{ redirect_to?: string; accepted_tos?: boolean } | undefined, unknown>> => {
      requests.push(path);
      if (path === "/api/auth/password/login") {
        expect(payload).toMatchObject({ email: "existing@example.com", password: "existing-password", invitation_token: "private-setup-token" });
        return axiosResponse({ redirect_to: "/accept-tos?redirect_url=%2F" });
      }
      if (path === "/api/authenticate") return axiosResponse({ accepted_tos: false });
      expect(payload).toEqual({ token: "private-setup-token" });
      return axiosResponse(undefined);
    });
    const client = mount("/account-setup", AccountSetup);
    expect(window.location.hash).toBe("");
    await screen.findByText("NATIVE_AUTH$EXISTING_ACCOUNT");
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
    expect(screen.queryByLabelText("NATIVE_AUTH$NEW_PASSWORD")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("NATIVE_AUTH$PASSWORD"), { target: { value: "existing-password" } });
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$SIGN_IN" }));
    await screen.findByText("TOS destination");
    expect(requests).toEqual(["/api/auth/password/login", "/api/auth/enrollment/accept-membership", "/api/authenticate"]);
    expect(enroll).not.toHaveBeenCalled();
    expect(JSON.stringify(localStorage)).not.toContain("private-setup-token");
    expect(JSON.stringify(sessionStorage)).not.toContain("private-setup-token");
    expect(JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state))).not.toMatch(/private-setup-token|existing-password/);
  });

  it("validates password confirmation and completes new-account setup", async () => {
    window.history.replaceState({}, "", "/account-setup#token=new-account-token");
    vi.spyOn(NativeAuthService, "inspect").mockResolvedValue({ email: "new@example.com", org_name: null, org_id: null, org_role_id: null, expires_at: "2099-01-01", action: "set_password" });
    const enroll = vi.spyOn(NativeAuthService, "enroll").mockResolvedValue({ action: "complete", redirect_to: "/accept-tos", accepted_tos: false });
    mount("/account-setup", AccountSetup);
    fireEvent.change(await screen.findByLabelText("NATIVE_AUTH$NEW_PASSWORD"), { target: { value: "a unique long password" } });
    fireEvent.change(screen.getByLabelText("NATIVE_AUTH$CONFIRM_PASSWORD"), { target: { value: "a different long password" } });
    fireEvent.submit(containingForm(screen.getByRole("button", { name: "NATIVE_AUTH$SET_UP_ACCOUNT" })));
    expect(await screen.findByRole("alert")).toHaveTextContent("NATIVE_AUTH$PASSWORD_MISMATCH");
    expect(enroll).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("NATIVE_AUTH$CONFIRM_PASSWORD"), { target: { value: "a unique long password" } });
    fireEvent.submit(containingForm(screen.getByRole("button", { name: "NATIVE_AUTH$SET_UP_ACCOUNT" })));
    await screen.findByText("TOS destination");
    expect(enroll).toHaveBeenCalledWith({ token: "new-account-token", password: "a unique long password" });
  });

  it("gives expired-link reissue guidance without leaking tokens", async () => {
    window.history.replaceState({}, "", "/account-setup#token=expired-token");
    vi.spyOn(NativeAuthService, "inspect").mockRejectedValue(new Error("expired"));
    mount("/account-setup", AccountSetup);
    await screen.findByText("NATIVE_AUTH$LINK_UNAVAILABLE");
    expect(window.location.hash).toBe("");
    expect(screen.queryByLabelText("NATIVE_AUTH$NEW_PASSWORD")).not.toBeInTheDocument();
    expect(screen.queryByText("expired-token")).not.toBeInTheDocument();
  });

  it("resets the password without automatically logging in", async () => {
    window.history.replaceState({}, "", "/password-reset#token=reset-token");
    const reset = vi.spyOn(NativeAuthService, "resetPassword").mockResolvedValue(undefined);
    const login = vi.spyOn(NativeAuthService, "login");
    const client = mount("/password-reset", PasswordReset);
    fireEvent.change(await screen.findByLabelText("NATIVE_AUTH$NEW_PASSWORD"), { target: { value: "new very long password" } });
    fireEvent.change(screen.getByLabelText("NATIVE_AUTH$CONFIRM_PASSWORD"), { target: { value: "new very long password" } });
    fireEvent.submit(containingForm(screen.getByRole("button", { name: "NATIVE_AUTH$RESET_PASSWORD" })));
    await screen.findByText("NATIVE_AUTH$PASSWORD_RESET_COMPLETE");
    expect(reset).toHaveBeenCalledWith({ token: "reset-token", new_password: "new very long password" });
    expect(login).not.toHaveBeenCalled();
    expect(window.location.hash).toBe("");
    expect(JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state))).not.toMatch(/reset-token|new very long password/);
  });

  it("masks show-once links from PostHog and supports manual copy fallback", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    const close = vi.fn();
    render(<AccountLinkModal link={{ invite_url: "https://app.test/account-setup#token=private-link", expires_at: "2099-01-01" }} onClose={close} />);
    expect(screen.getByRole("dialog").querySelector(".ph-no-capture")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$COPY_LINK" }));
    await screen.findByText("NATIVE_AUTH$COPY_MANUALLY");
    fireEvent.click(screen.getByRole("button", { name: "NATIVE_AUTH$CLOSE" }));
    expect(close).toHaveBeenCalledOnce();
  });
});
