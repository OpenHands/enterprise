import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SettingsNavUserMenu } from "#/components/features/settings/settings-nav-user-menu";

const logoutMutate = vi.fn();

vi.mock("#/hooks/query/use-git-user", () => ({
  useGitUser: () => ({
    data: { avatar_url: "https://example.com/avatar.png", login: "neo-user" },
    isFetching: false,
  }),
}));

vi.mock("#/hooks/query/use-settings", () => ({
  useSettings: () => ({
    data: { email: "neo@example.com" },
  }),
}));

vi.mock("#/hooks/mutation/use-logout", () => ({
  useLogout: () => ({ mutate: logoutMutate }),
}));

vi.mock("#/hooks/use-app-mode", () => ({
  useAppMode: () => ({ isSaas: true, isEnterpriseCloud: true }),
}));

describe("SettingsNavUserMenu", () => {
  beforeEach(() => {
    logoutMutate.mockClear();
  });

  it("renders the user trigger with email", () => {
    render(<SettingsNavUserMenu />);

    expect(screen.getByTestId("settings-nav-user-menu")).toBeInTheDocument();
    expect(screen.getByText("neo@example.com")).toBeInTheDocument();
  });

  it("opens a popover with documentation and logout", async () => {
    const user = userEvent.setup();
    render(<SettingsNavUserMenu />);

    await user.click(screen.getByTestId("settings-nav-user-trigger"));

    expect(screen.getByTestId("settings-nav-user-popover")).toBeInTheDocument();
    const docs = screen.getByRole("menuitem", { name: "SIDEBAR$DOCS" });
    expect(docs).toHaveAttribute("href", "https://docs.openhands.dev");
    expect(screen.getByText("ACCOUNT_SETTINGS$LOGOUT")).toBeInTheDocument();
  });

  it("logs out when logout is clicked", async () => {
    const user = userEvent.setup();
    render(<SettingsNavUserMenu />);

    await user.click(screen.getByTestId("settings-nav-user-trigger"));
    await user.click(screen.getByText("ACCOUNT_SETTINGS$LOGOUT"));

    expect(logoutMutate).toHaveBeenCalledTimes(1);
  });
});
