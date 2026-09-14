import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NativeAuthService } from "#/api/native-auth-service/native-auth-service.api";
import { PasswordChange } from "#/components/features/native-auth/password-change";
import { containingForm } from "../helpers/native-fixtures";

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({ data: { auth_mode: "native", login_methods: ["password"], app_mode: "saas" } }),
}));
vi.mock("#/hooks/query/use-is-authed", () => ({ useIsAuthed: () => ({ data: true }) }));

function mount(): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Routes = createRoutesStub([{ path: "/settings/user", Component: PasswordChange }]);
  render(<QueryClientProvider client={client}><Routes initialEntries={["/settings/user"]} /></QueryClientProvider>);
  return client;
}

describe("password change", () => {
  afterEach(() => vi.restoreAllMocks());

  it("checks confirmation, changes the password, and clears all credential inputs", async () => {
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "member", email: "member@example.test", has_password: true, global_permissions: [] });
    const change = vi.spyOn(NativeAuthService, "changePassword").mockResolvedValue(undefined);
    const client = mount();
    const current = await screen.findByLabelText("AUTH$CURRENT_PASSWORD");
    const password = screen.getByLabelText("AUTH$NEW_PASSWORD");
    const confirmation = screen.getByLabelText("AUTH$CONFIRM_PASSWORD");
    fireEvent.change(current, { target: { value: "old-private-password" } });
    fireEvent.change(password, { target: { value: "new-private-password" } });
    fireEvent.change(confirmation, { target: { value: "mismatching-password" } });
    fireEvent.submit(containingForm(screen.getByRole("button", { name: "AUTH$CHANGE_PASSWORD" })));
    expect(await screen.findByRole("alert")).toHaveTextContent("AUTH$PASSWORD_MISMATCH");
    expect(change).not.toHaveBeenCalled();
    fireEvent.change(confirmation, { target: { value: "new-private-password" } });
    fireEvent.submit(containingForm(screen.getByRole("button", { name: "AUTH$CHANGE_PASSWORD" })));
    await screen.findByText("AUTH$PASSWORD_CHANGED");
    expect(change).toHaveBeenCalledWith({ current_password: "old-private-password", new_password: "new-private-password" });
    for (const input of [current, password, confirmation]) expect(input).toHaveValue("");
    expect(JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state))).not.toContain("private-password");
  });
});
