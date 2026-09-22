import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SettingsNavUserMenu } from "#/components/features/settings/settings-nav-user-menu";

const logoutMutate = vi.fn();

const gitUserMocks = vi.hoisted(() => ({
  useGitUser: vi.fn(),
}));
vi.mock("#/hooks/query/use-git-user", () => gitUserMocks);

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
    gitUserMocks.useGitUser.mockReturnValue({
      data: { avatar_url: "https://example.com/avatar.png", login: "neo-user" },
      isFetching: false,
    });
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

  it("displays the user's profile avatar when one is available", () => {
    render(<SettingsNavUserMenu />);

    expect(screen.getByAltText("AVATAR$ALT_TEXT")).toHaveAttribute(
      "src",
      "https://example.com/avatar.png",
    );
    expect(
      screen.queryByLabelText("USER$AVATAR_PLACEHOLDER"),
    ).not.toBeInTheDocument();
  });

  it("falls back to the placeholder avatar when no profile picture is available", () => {
    gitUserMocks.useGitUser.mockReturnValue({
      data: undefined,
      isFetching: false,
    });

    render(<SettingsNavUserMenu />);

    expect(
      screen.getByLabelText("USER$AVATAR_PLACEHOLDER"),
    ).toBeInTheDocument();
    expect(screen.queryByAltText("AVATAR$ALT_TEXT")).not.toBeInTheDocument();
  });

  it("shows a loading spinner while the user profile is being fetched", () => {
    gitUserMocks.useGitUser.mockReturnValue({
      data: undefined,
      isFetching: true,
    });

    render(<SettingsNavUserMenu />);

    expect(screen.getByTestId("loading-spinner")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("USER$AVATAR_PLACEHOLDER"),
    ).not.toBeInTheDocument();
  });
});
