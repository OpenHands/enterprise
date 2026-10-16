import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgDefaultLlmSettingsScreen from "./org-default-llm-settings";
import { ConfigurationOperation } from "#/types/settings";

const mockState = vi.hoisted(() => ({
  role: "member",
  operation: "extend" as ConfigurationOperation,
  saveSettings: vi.fn(),
}));

vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => ({ data: { role: mockState.role } }),
}));

vi.mock("#/hooks/query/use-settings", () => ({
  useSettings: () => ({
    data: {
      configuration_policy: { llm: { operation: mockState.operation } },
    },
  }),
}));

vi.mock("#/hooks/mutation/use-save-settings", () => ({
  useSaveSettings: () => ({
    mutateAsync: mockState.saveSettings,
    isPending: false,
  }),
}));

vi.mock(
  "#/components/features/super-admin/super-admin-group-setup-modal",
  () => ({
    SuperAdminGroupSetupModal: () => null,
  }),
);

vi.mock("./llm-settings", () => ({
  LlmSettingsScreen: ({
    scope,
    canActivateOrgProfiles = false,
    showOrgManagedBadges = false,
    showReadOnlyProviderConnections = false,
  }: {
    scope: string;
    canActivateOrgProfiles?: boolean;
    showOrgManagedBadges?: boolean;
    showReadOnlyProviderConnections?: boolean;
  }) => (
    <div
      data-testid={`llm-settings-${scope}`}
      data-can-activate={String(canActivateOrgProfiles)}
      data-org-managed-badges={String(showOrgManagedBadges)}
      data-read-only-connections={String(showReadOnlyProviderConnections)}
    />
  ),
}));

vi.mock("#/utils/org/permission-guard", () => ({
  createPermissionGuard: () => vi.fn(),
}));

vi.mock("#/utils/custom-toast-handlers", () => ({
  displayErrorToast: vi.fn(),
  displaySuccessToast: vi.fn(),
}));

const renderScreen = () => render(<OrgDefaultLlmSettingsScreen />);

const orgSettings = () => screen.queryByTestId("llm-settings-org");
const personalSettings = () => screen.queryByTestId("llm-settings-personal");

beforeEach(() => {
  mockState.role = "member";
  mockState.operation = "extend";
  mockState.saveSettings.mockReset();
  mockState.saveSettings.mockResolvedValue(undefined);
});

describe("OrgDefaultLlmSettingsScreen", () => {
  it("gives admins organization and personal tabs", () => {
    mockState.role = "admin";
    renderScreen();

    expect(orgSettings()).toBeInTheDocument();
    expect(personalSettings()).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Locked/ })).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("tab", { name: "SETTINGS$PERSONAL_SETTINGS_HEADER" }),
    );

    expect(orgSettings()).not.toBeInTheDocument();
    expect(personalSettings()).toBeInTheDocument();
  });

  it("saves the selected member operation from the admin view", () => {
    mockState.role = "owner";
    renderScreen();

    fireEvent.click(screen.getByRole("button", { name: /^Subset/ }));

    expect(mockState.saveSettings).toHaveBeenCalledWith({
      configuration_policy: { llm: { operation: "subset" } },
    });
  });

  it("renders the managed organization experience for locked members", () => {
    mockState.operation = "locked";
    renderScreen();

    expect(screen.getByTestId("locked-member-notice")).toHaveTextContent(
      "SETTINGS$LOCKED_LLM_NOTICE",
    );
    expect(orgSettings()).toHaveAttribute("data-can-activate", "true");
    expect(orgSettings()).toHaveAttribute("data-org-managed-badges", "true");
    expect(orgSettings()).toHaveAttribute("data-read-only-connections", "true");
    expect(personalSettings()).not.toBeInTheDocument();
  });

  it.each([
    ["locked", true, false, "true"],
    ["subset", true, false, "true"],
    ["replace", false, true, null],
    ["extend", true, true, "true"],
  ] as const)(
    "renders member controls for %s",
    (operation, showsOrg, showsPersonal, canActivate) => {
      mockState.operation = operation;
      renderScreen();

      if (showsOrg) {
        expect(orgSettings()).toBeInTheDocument();
        expect(orgSettings()).toHaveAttribute("data-can-activate", canActivate);
      } else {
        expect(orgSettings()).not.toBeInTheDocument();
      }

      if (showsPersonal) {
        expect(personalSettings()).toBeInTheDocument();
      } else {
        expect(personalSettings()).not.toBeInTheDocument();
      }

      expect(
        screen.queryByRole("button", { name: "Locked" }),
      ).not.toBeInTheDocument();
    },
  );
});
