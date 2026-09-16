import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import NativeLoginPage from "#/routes/native-login";
import { NativeAuthService } from "#/api/native-auth-service/native-auth-service.api";

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({ data: { auth_mode: "native", app_mode: "saas", login_methods: ["password"] }, isLoading: false }),
}));
vi.mock("#/hooks/query/use-is-authed", () => ({
  useIsAuthed: () => ({ data: false, isLoading: false }),
}));

function mount(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["settings", "previous-user"], { email: "previous@example.test" });
  const Routes = createRoutesStub([
    { path: "/login", Component: NativeLoginPage },
    { path: "/accept-tos", Component: () => <p>Accept terms</p> },
  ]);
  render(<StrictMode><QueryClientProvider client={client}><Routes initialEntries={["/login"]} /></QueryClientProvider></StrictMode>);
  return client;
}

describe("password login", () => {
  afterEach(() => vi.restoreAllMocks());

  it("signs in with an email, clears previous account data, and continues to terms", async () => {
    const login = vi.spyOn(NativeAuthService, "login").mockResolvedValue({ redirect_to: "/accept-tos", accepted_tos: false });
    const client = mount();
    fireEvent.change(screen.getByLabelText("AUTH$EMAIL"), { target: { value: "member@example.test" } });
    fireEvent.change(screen.getByLabelText("AUTH$PASSWORD"), { target: { value: "private-password" } });
    fireEvent.click(screen.getByRole("button", { name: "AUTH$SIGN_IN" }));
    await screen.findByText("Accept terms");
    expect(login).toHaveBeenCalledWith({ email: "member@example.test", password: "private-password", return_path: "/" });
    expect(client.getQueryData(["settings", "previous-user"])).toBeUndefined();
    expect(JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state))).not.toContain("private-password");
  });

  it("clears a rejected password and allows retry", async () => {
    vi.spyOn(NativeAuthService, "login").mockRejectedValue(new Error("Invalid credentials"));
    mount();
    fireEvent.change(screen.getByLabelText("AUTH$EMAIL"), { target: { value: "member@example.test" } });
    const password = screen.getByLabelText("AUTH$PASSWORD");
    fireEvent.change(password, { target: { value: "rejected-password" } });
    fireEvent.click(screen.getByRole("button", { name: "AUTH$SIGN_IN" }));
    await screen.findByRole("alert");
    expect(password).toHaveValue("");
    expect(screen.getByRole("button", { name: "AUTH$SIGN_IN" })).toBeEnabled();
  });
});
