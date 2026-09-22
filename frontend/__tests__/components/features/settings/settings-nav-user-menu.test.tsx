import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MemoryRouter } from "react-router";
import { SettingsNavUserMenu } from "#/components/features/settings/settings-nav-user-menu";

const logoutMutate = vi.fn();
const mockMe = vi.hoisted(() => ({
  data: { permissions: ["create_organization"] } as {
    permissions?: string[];
  } | null,
}));

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

vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => mockMe,
}));

const mockAccountSettings = vi.hoisted(() => ({
  items: [] as Array<{
    type: "item";
    item: {
      to: string;
      text: string;
      subtitle: string;
      icon: null;
      menuOnly: boolean;
    };
  }>,
}));

vi.mock("#/hooks/use-settings-nav-items", () => ({
  useSettingsNavItems: () => mockAccountSettings.items,
  getSettingsUserMenuItems: (
    items: Array<{ type: string; item?: { menuOnly?: boolean } }>,
  ) =>
    items.flatMap((item) =>
      item.type === "item" && item.item?.menuOnly ? [item.item] : [],
    ),
}));

const renderMenu = () =>
  render(
    <MemoryRouter>
      <SettingsNavUserMenu />
    </MemoryRouter>,
  );

const defaultAccountSettings = [
  {
    type: "item" as const,
    item: {
      to: "/settings/user",
      text: "SETTINGS$NAV_USER",
      subtitle: "SETTINGS$PAGE_USER_SUBLINE",
      icon: null,
      menuOnly: true,
    },
  },
  {
    type: "item" as const,
    item: {
      to: "/settings/app",
      text: "SETTINGS$NAV_APPLICATION",
      subtitle: "SETTINGS$PAGE_APPLICATION_SUBLINE",
      icon: null,
      menuOnly: true,
    },
  },
];

describe("SettingsNavUserMenu", () => {
  beforeEach(() => {
    logoutMutate.mockClear();
    mockMe.data = { permissions: ["create_organization"] };
    mockAccountSettings.items = [...defaultAccountSettings];
  });

  it("renders the user trigger with email", () => {
    renderMenu();

    expect(screen.getByTestId("settings-nav-user-menu")).toBeInTheDocument();
    expect(screen.getByText("neo@example.com")).toBeInTheDocument();
  });

  it("opens a popover with Super Admin first, then User, Application, docs, and logout", async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByTestId("settings-nav-user-trigger"));

    expect(screen.getByTestId("settings-nav-user-popover")).toBeInTheDocument();
    const menuItems = screen.getAllByRole("menuitem");
    expect(menuItems[0]).toHaveAttribute(
      "data-testid",
      "settings-nav-super-admin",
    );
    expect(menuItems[0]).toHaveAttribute("href", "/super-admin");
    expect(menuItems[1]).toHaveAttribute(
      "data-testid",
      "settings-nav-account-user",
    );
    expect(menuItems[2]).toHaveAttribute(
      "data-testid",
      "settings-nav-account-app",
    );
    expect(menuItems[3]).toHaveAttribute(
      "href",
      "https://docs.openhands.dev",
    );
    expect(screen.getByText("ACCOUNT_SETTINGS$LOGOUT")).toBeInTheDocument();
  });

  it("hides User when the users page is not in the nav", async () => {
    mockAccountSettings.items = mockAccountSettings.items.filter(
      (item) => item.item.to !== "/settings/user",
    );
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByTestId("settings-nav-user-trigger"));

    expect(
      screen.queryByTestId("settings-nav-account-user"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("settings-nav-account-app")).toBeInTheDocument();
  });

  it("hides Super Admin when the user is not an instance admin", async () => {
    mockMe.data = { permissions: [] };
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByTestId("settings-nav-user-trigger"));

    expect(
      screen.queryByTestId("settings-nav-super-admin"),
    ).not.toBeInTheDocument();
  });

  it("logs out when logout is clicked", async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByTestId("settings-nav-user-trigger"));
    await user.click(screen.getByText("ACCOUNT_SETTINGS$LOGOUT"));

    expect(logoutMutate).toHaveBeenCalledTimes(1);
  });
});
