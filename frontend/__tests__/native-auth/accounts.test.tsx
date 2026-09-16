import type { ComponentProps } from "react";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import AdminUsers from "#/routes/admin-users";
import { NativeAuthService, NativeAuthError, NativeAccount } from "#/api/native-auth-service/native-auth-service.api";
import { useSettingsNavItems } from "#/hooks/use-settings-nav-items";

// Exercise the controller's approved selections; the shared autocomplete is covered in browser QA.
vi.mock("#/components/features/settings/settings-dropdown-input", () => ({
  SettingsDropdownInput: ({ label, items, selectedKey, isDisabled, onSelectionChange }: ComponentProps<typeof SettingsDropdownInput>): React.JSX.Element => (
    <label>{label}<select value={selectedKey ?? ""} disabled={isDisabled}
      onChange={(event): void => onSelectionChange?.(event.currentTarget.value)}>
      <option value="" />{items.map((item) => <option key={item.key} value={String(item.key)}>{item.label}</option>)}
    </select></label>
  ),
}));

vi.mock("#/hooks/query/use-config", () => ({ useConfig: () => ({ data: { auth_mode: "native", login_methods: ["password"], app_mode: "saas", providers_configured: [], feature_flags: {} }, isLoading: false }) }));
vi.mock("#/hooks/query/use-is-authed", () => ({ useIsAuthed: () => ({ data: true }) }));
vi.mock("#/hooks/query/use-organizations", () => ({ useOrganizations: () => ({ data: { organizations: [{ id: "personal", name: "Personal", is_personal: true }] } }) }));
vi.mock("#/hooks/query/use-me", () => ({ useMe: () => ({ data: { role: "owner", email: "admin@example.com" } }) }));
vi.mock("#/hooks/query/use-settings", () => ({ useSettings: () => ({ data: {} }) }));
vi.mock("#/hooks/use-org-type-and-access", () => ({ useOrgTypeAndAccess: () => ({ isPersonalOrg: true, isTeamOrg: false, organizationId: "personal" }) }));

const account: NativeAccount = { id: "account-id", email: "member@example.com", state: "profile_present", profile_present: true, is_disabled: false, role_id: null, created_at: "2026-01-01", pending_invitations: [] };

function mount(Component: () => React.ReactNode, path: string = "/settings/users"): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Routes = createRoutesStub([{ path, Component }, { path: "/settings", Component: () => <p>Other settings</p> }]);
  render(<QueryClientProvider client={client}><Routes initialEntries={[path]} /></QueryClientProvider>);
  return client;
}

describe("native Users administration", () => {
  afterEach(() => vi.restoreAllMocks());
  it("exposes Users from the personal org using global permission", async () => {
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "admin", global_permissions: ["manage_users"] });
    function Navigation(): React.JSX.Element {
      const items = useSettingsNavItems();
      return <>{items.filter((item) => item.type === "item").map((item) => item.type === "item" && <span key={item.item.to}>{item.item.to}</span>)}</>;
    }
    mount(Navigation);
    await screen.findByText("/settings/users");
    expect(screen.queryByText("/settings/org-members")).not.toBeInTheDocument();
  });

  it("does not infer global Users authority from an org owner role", async () => {
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "owner", global_permissions: [] });
    const list = vi.spyOn(NativeAuthService, "accounts");
    const teams = vi.spyOn(NativeAuthService, "invitationOrganizations");
    mount(AdminUsers);
    await screen.findByText("Other settings");
    expect(list).not.toHaveBeenCalled();
    expect(teams).not.toHaveBeenCalled();
  });

  it("loads every admin team page and invites an initial owner outside the membership list", async () => {
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "admin", global_permissions: ["manage_users"] });
    vi.spyOn(NativeAuthService, "accounts").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "invitations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "roles").mockResolvedValue([{ id: 1, name: "owner" }]);
    const teams = vi.spyOn(NativeAuthService, "invitationOrganizations").mockImplementation(async (offset) => ({
      items: offset === 0 ? Array.from({ length: 100 }, (_, i) => ({ id: `team-${i}`, name: `Team ${i}` })) : [{ id: "new-team", name: "New unseeded team" }], total: 101,
    }));
    const invite = vi.spyOn(NativeAuthService, "invite").mockResolvedValue({ invite_url: "https://app.test/account-setup#token=team-setup", expires_at: "2099-01-01" });
    mount(AdminUsers);
    await waitFor(() => expect(screen.getByLabelText("AUTH$OPTIONAL_ORG")).not.toBeDisabled());

    await screen.findByRole("option", { name: "New unseeded team" });
    expect(teams).toHaveBeenCalledWith(0);
    expect(teams).toHaveBeenCalledWith(100);
    expect(screen.queryByRole("option", { name: "Personal" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("AUTH$OPTIONAL_ORG"), { target: { value: "new-team" } });
    fireEvent.change(screen.getByLabelText("AUTH$ORG_ROLE"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("AUTH$EMAIL"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "AUTH$CREATE_SETUP_LINK" }));
    await waitFor(() => expect(invite).toHaveBeenCalledWith({ email: "owner@example.com", org_id: "new-team", org_role_id: 1 }));
    await screen.findByDisplayValue("https://app.test/account-setup#token=team-setup");
  });

  it("issues a reset link without another login and keeps it out of cached results", async () => {
    vi.spyOn(NativeAuthService, "invitationOrganizations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "admin", email: "admin@example.com", global_permissions: ["manage_users"] });
    vi.spyOn(NativeAuthService, "accounts").mockResolvedValue({ items: [account], total: 1 });
    vi.spyOn(NativeAuthService, "account").mockResolvedValue(account);
    vi.spyOn(NativeAuthService, "invitations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "roles").mockResolvedValue([{ id: 3, name: "member" }]);
    const issue = vi.spyOn(NativeAuthService, "issueReset").mockResolvedValue({ reset_url: "https://app.test/password-reset#token=only-once", expires_at: "2099-01-01" });
    const login = vi.spyOn(NativeAuthService, "login");
    const client = mount(AdminUsers);
    fireEvent.click(await screen.findByRole("button", { name: "member@example.com" }));
    fireEvent.click(await screen.findByRole("button", { name: "AUTH$CREATE_RESET_LINK" }));
    await screen.findByDisplayValue("https://app.test/password-reset#token=only-once");
    expect(issue).toHaveBeenCalledTimes(1);
    expect(login).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("AUTH$PASSWORD")).not.toBeInTheDocument();
    expect(JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state))).not.toMatch(/only-once|admin-password/);
    fireEvent.click(screen.getByRole("button", { name: "AUTH$CLOSE" }));
    expect(screen.queryByDisplayValue("https://app.test/password-reset#token=only-once")).not.toBeInTheDocument();
  });

  it("reports a rejected last-administrator change after confirmation", async () => {
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "admin", global_permissions: ["manage_users"] });
    vi.spyOn(NativeAuthService, "accounts").mockResolvedValue({ items: [account], total: 1 });
    vi.spyOn(NativeAuthService, "account").mockResolvedValue(account);
    vi.spyOn(NativeAuthService, "invitations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "invitationOrganizations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "roles").mockResolvedValue([]);
    const superadmins = vi.spyOn(NativeAuthService, "superadmins");
    const lifecycle = vi.spyOn(NativeAuthService, "lifecycle").mockRejectedValue(new NativeAuthError(409, "Cannot remove the last active superadmin"));
    mount(AdminUsers);
    fireEvent.click(await screen.findByRole("button", { name: "member@example.com" }));
    fireEvent.click(await screen.findByRole("button", { name: "AUTH$DISABLE" }));
    expect(lifecycle).not.toHaveBeenCalled();
    expect(superadmins).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "AUTH$GRANT_ADMIN" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "AUTH$CONFIRM" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot remove the last active superadmin");
    expect(lifecycle).toHaveBeenCalledWith({ id: "account-id", action: "disable" });
  });

  it("requires administrator-management permission and confirmation to grant access", async () => {
    vi.spyOn(NativeAuthService, "profile").mockResolvedValue({ id: "admin", global_permissions: ["manage_users", "manage_super_admins"] });
    vi.spyOn(NativeAuthService, "accounts").mockResolvedValue({ items: [account], total: 1 });
    vi.spyOn(NativeAuthService, "account").mockResolvedValue(account);
    vi.spyOn(NativeAuthService, "invitations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "invitationOrganizations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(NativeAuthService, "roles").mockResolvedValue([]);
    vi.spyOn(NativeAuthService, "superadmins").mockResolvedValue({ super_admins: [{ user_id: "admin", email: null }] });
    const grant = vi.spyOn(NativeAuthService, "setSuperadmin").mockResolvedValue(undefined);
    mount(AdminUsers);
    fireEvent.click(await screen.findByRole("button", { name: "member@example.com" }));
    const button = await screen.findByRole("button", { name: "AUTH$GRANT_ADMIN" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect(grant).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "AUTH$CONFIRM" }));
    await waitFor(() => expect(grant).toHaveBeenCalledWith({ id: "account-id", enabled: true }));
  });
});
