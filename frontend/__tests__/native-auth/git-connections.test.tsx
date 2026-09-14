import { containingForm } from "../helpers/native-fixtures";
import type { ComponentProps } from "react";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NativeGitSettings } from "#/components/features/settings/git-settings/native-git-settings";
import { GitConnectionService } from "#/api/git-connection-service/git-connection-service.api";
import AuthService from "#/api/auth-service/auth-service.api";

// Exercise the controller's approved selections; the shared autocomplete is covered in browser QA.
vi.mock("#/components/features/settings/settings-dropdown-input", () => ({
  SettingsDropdownInput: ({
    label,
    items,
    selectedKey,
    isDisabled,
    onSelectionChange,
  }: ComponentProps<typeof SettingsDropdownInput>): React.JSX.Element => (
    <label>
      {label}
      <select
        value={selectedKey ?? ""}
        disabled={isDisabled}
        onChange={(event): void =>
          onSelectionChange?.(event.currentTarget.value)
        }
      >
        <option value="" />
        {items.map((item) => (
          <option key={item.key} value={String(item.key)}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  ),
}));

vi.mock("#/hooks/query/use-config", () => ({
  useConfig: () => ({
    data: {
      auth_mode: "native",
      app_mode: "saas",
      providers_configured: [],
      feature_flags: {},
    },
    isLoading: false,
  }),
}));
vi.mock("#/hooks/query/use-is-authed", () => ({
  useIsAuthed: () => ({ data: true }),
}));
vi.mock("#/hooks/query/use-organizations", () => ({
  useOrganizations: () => ({
    data: {
      organizations: [{ id: "personal", name: "Personal", is_personal: true }],
    },
  }),
}));
vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => ({ data: { role: "owner", email: "admin@example.com" } }),
}));
vi.mock("#/hooks/query/use-settings", () => ({
  useSettings: () => ({ data: {} }),
}));
vi.mock("#/hooks/use-org-type-and-access", () => ({
  useOrgTypeAndAccess: () => ({
    isPersonalOrg: true,
    isTeamOrg: false,
    organizationId: "personal",
  }),
}));

function mount(
  Component: () => React.ReactNode,
  path: string = "/settings/users",
): QueryClient {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Routes = createRoutesStub([
    { path, Component },
    { path: "/settings", Component: () => <p>Other settings</p> },
  ]);
  render(
    <QueryClientProvider client={client}>
      <Routes initialEntries={[path]} />
    </QueryClientProvider>,
  );
  return client;
}

describe("native Git connections", () => {
  afterEach(() => vi.restoreAllMocks());
  it("connects a GitHub PAT without a login broker and clears the submitted credential", async () => {
    vi.spyOn(GitConnectionService, "list").mockResolvedValue({
      connections: [],
      capabilities: {
        github: {
          methods: ["pat"],
          hosts: ["github.com"],
          default_host: "github.com",
        },
      },
    });
    const save = vi
      .spyOn(GitConnectionService, "save")
      .mockResolvedValue(undefined);
    const authorize = vi.spyOn(GitConnectionService, "authorize");
    const logout = vi.spyOn(AuthService, "logout");
    const client = mount(NativeGitSettings, "/settings/integrations");
    await screen.findByLabelText("NATIVE_GIT$PAT");
    expect(screen.getByText("NATIVE_GIT$OPTIONAL_HELP")).toBeInTheDocument();
    expect(authorize).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("NATIVE_GIT$PAT"), {
      target: { value: "private-github-token" },
    });
    fireEvent.submit(containingForm(screen.getByLabelText("NATIVE_GIT$PAT")));
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        provider: "github",
        host: "github.com",
        token: "private-github-token",
      }),
    );
    await screen.findByText("NATIVE_GIT$SAVED");
    expect(screen.getByLabelText("NATIVE_GIT$PAT")).toHaveValue("");
    expect(
      JSON.stringify(
        client
          .getMutationCache()
          .getAll()
          .map((mutation) => mutation.state),
      ),
    ).not.toContain("private-github-token");
    expect(logout).not.toHaveBeenCalled();
  });

  it("keeps connected providers compact and clears cancelled or saved credential edits", async () => {
    vi.spyOn(GitConnectionService, "list").mockResolvedValue({
      connections: [
        {
          provider: "github",
          host: "github.com",
          auth_type: "pat",
          status: "connected",
          account: {
            id: "1",
            login: "coder",
            display_name: null,
            avatar_url: null,
          },
          last_error: null,
        },
      ],
      capabilities: {
        github: {
          methods: ["pat", "oauth"],
          hosts: ["github.com"],
          default_host: "github.com",
        },
      },
    });
    const save = vi
      .spyOn(GitConnectionService, "save")
      .mockResolvedValue(undefined);
    const client = mount(NativeGitSettings, "/settings/integrations");
    const edit = await screen.findByRole("button", {
      name: "NATIVE_GIT$RECONNECT",
    });
    expect(screen.queryByLabelText("NATIVE_GIT$PAT")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "NATIVE_GIT$CONNECT_OAUTH" }),
    ).not.toBeInTheDocument();
    fireEvent.click(edit);
    fireEvent.change(screen.getByLabelText("NATIVE_GIT$PAT"), {
      target: { value: "cancelled-secret" },
    });
    expect(
      screen.getByRole("button", { name: "NATIVE_GIT$CONNECT_OAUTH" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "BUTTON$CANCEL" }));
    expect(screen.queryByLabelText("NATIVE_GIT$PAT")).not.toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "NATIVE_GIT$RECONNECT" }),
    );
    expect(screen.getByLabelText("NATIVE_GIT$PAT")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("NATIVE_GIT$PAT"), {
      target: { value: "replacement-secret" },
    });
    fireEvent.submit(containingForm(screen.getByLabelText("NATIVE_GIT$PAT")));
    await screen.findByText("NATIVE_GIT$SAVED");
    expect(save).toHaveBeenCalledWith({
      provider: "github",
      host: "github.com",
      token: "replacement-secret",
    });
    expect(screen.queryByLabelText("NATIVE_GIT$PAT")).not.toBeInTheDocument();
    expect(
      JSON.stringify(
        client
          .getMutationCache()
          .getAll()
          .map((mutation) => mutation.state),
      ),
    ).not.toMatch(/cancelled-secret|replacement-secret/);
  });

  it("shows reconnect choices without repeating an absent account label", async () => {
    vi.spyOn(GitConnectionService, "list").mockResolvedValue({
      connections: [
        {
          provider: "github",
          host: "github.com",
          auth_type: "pat",
          status: "reconnect_required",
          account: {
            id: null,
            login: null,
            display_name: null,
            avatar_url: null,
          },
          last_error: "expired",
        },
      ],
      capabilities: {
        github: {
          methods: ["pat", "oauth"],
          hosts: ["github.com"],
          default_host: "github.com",
        },
      },
    });
    mount(NativeGitSettings, "/settings/integrations");
    await screen.findByLabelText("NATIVE_GIT$PAT");
    expect(screen.getAllByText("github.com")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "NATIVE_GIT$CONNECT_OAUTH" }),
    ).toBeInTheDocument();
  });

  it("disconnects only Git credentials and preserves application authentication", async () => {
    vi.spyOn(GitConnectionService, "list").mockResolvedValue({
      connections: [
        {
          provider: "github",
          host: "github.com",
          auth_type: "pat",
          status: "connected",
          account: {
            id: "1",
            login: "coder",
            display_name: null,
            avatar_url: null,
          },
          last_error: null,
        },
      ],
      capabilities: {
        github: {
          methods: ["pat"],
          hosts: ["github.com"],
          default_host: "github.com",
        },
      },
    });
    const disconnect = vi
      .spyOn(GitConnectionService, "disconnect")
      .mockResolvedValue(undefined);
    const logout = vi.spyOn(AuthService, "logout");
    const client = mount(NativeGitSettings, "/settings/integrations");
    client.setQueryData(["user", "authenticated", "saas"], true);
    fireEvent.click(
      await screen.findByRole("button", { name: "BUTTON$DISCONNECT" }),
    );
    expect(disconnect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "BUTTON$CONFIRM" }));
    await waitFor(() =>
      expect(disconnect).toHaveBeenCalledWith({
        provider: "github",
        host: "github.com",
      }),
    );
    expect(client.getQueryData(["user", "authenticated", "saas"])).toBe(true);
    expect(logout).not.toHaveBeenCalled();
  });
});
