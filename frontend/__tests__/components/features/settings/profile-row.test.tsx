import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LlmProfileSummary } from "#/api/settings-service/profiles-service.api";
import { ProfileRow } from "#/components/features/settings/profile-row";
import { useFreeModelsStore } from "#/stores/free-models-store";

const mockState = vi.hoisted(() => ({ hostedDomain: true }));

vi.mock("#/utils/domain-gate", () => ({
  isOpenHandsHostedDomain: () => mockState.hostedDomain,
}));

const profile: LlmProfileSummary = {
  name: "openai_gpt-4o",
  model: "openai/gpt-4o",
  base_url: null,
  api_key_set: true,
};

function renderRow(
  overrides: Partial<React.ComponentProps<typeof ProfileRow>> = {},
) {
  const props = {
    profile,
    isActive: false,
    onActivate: vi.fn(),
    onEdit: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    isActivating: false,
    ...overrides,
  };
  return {
    // eslint-disable-next-line react/jsx-props-no-spreading
    ...render(<ProfileRow {...props} />),
    props,
  };
}

describe("ProfileRow", () => {
  it("renders the profile name and model", () => {
    renderRow();

    expect(screen.getByText("openai_gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-4o")).toBeInTheDocument();
    expect(
      screen.queryByTestId("profile-active-badge"),
    ).not.toBeInTheDocument();
  });

  it("shows the active badge when isActive is true", () => {
    renderRow({ isActive: true });

    expect(screen.getByTestId("profile-active-badge")).toHaveTextContent(
      "SETTINGS$PROFILE_ACTIVE_BADGE",
    );
  });

  it("allows the row to grow past the shared fixed list height", () => {
    renderRow({ isActive: true });

    expect(screen.getByTestId("profile-row")).toHaveClass("h-auto");
  });

  it("opens the actions menu when the trigger is clicked", async () => {
    renderRow();
    const user = userEvent.setup();

    expect(screen.queryByTestId("profile-edit")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("profile-menu-trigger"));
    expect(screen.getByTestId("profile-edit")).toBeInTheDocument();
  });

  it("hides the actions menu trigger when management is disabled", () => {
    renderRow({ canManage: false });

    expect(screen.getByText("openai_gpt-4o")).toBeInTheDocument();
    expect(
      screen.queryByTestId("profile-menu-trigger"),
    ).not.toBeInTheDocument();
  });

  describe("free-model label", () => {
    const SUFFIX = "SETTINGS$FREE_MODEL_SUFFIX";

    const freeProfile: LlmProfileSummary = {
      name: "my-profile",
      model: "openhands/free-model",
      base_url: null,
      api_key_set: false,
    };

    beforeEach(() => {
      mockState.hostedDomain = true;
      useFreeModelsStore.setState({
        freeModels: new Set(["openhands/free-model"]),
        defaultModel: null,
        defaultModelReady: true,
      });
    });

    it("appends the free label to a free model on a hosted domain", () => {
      renderRow({ profile: freeProfile });
      expect(
        screen.getByText(`openhands/free-model${SUFFIX}`),
      ).toBeInTheDocument();
    });

    it("does not append the free label to a paid model on a hosted domain", () => {
      renderRow({ profile: { ...freeProfile, model: "openhands/paid-model" } });
      expect(screen.getByText("openhands/paid-model")).toBeInTheDocument();
      expect(
        screen.queryByText(`openhands/paid-model${SUFFIX}`),
      ).not.toBeInTheDocument();
    });

    it("does not show any free-model label on a non-hosted domain", () => {
      mockState.hostedDomain = false;
      renderRow({ profile: freeProfile });
      expect(screen.getByText("openhands/free-model")).toBeInTheDocument();
      expect(
        screen.queryByText(`openhands/free-model${SUFFIX}`),
      ).not.toBeInTheDocument();
    });
  });
});
