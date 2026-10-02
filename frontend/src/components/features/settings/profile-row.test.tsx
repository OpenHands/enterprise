import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProfileRow } from "./profile-row";
import type { LlmProfileSummary } from "#/api/settings-service/profiles-service.api";
import { useFreeModelsStore } from "#/stores/free-models-store";

const mockState = vi.hoisted(() => ({
  hostedDomain: true,
}));

vi.mock("#/utils/domain-gate", () => ({
  isOpenHandsHostedDomain: () => mockState.hostedDomain,
}));

const baseProfile: LlmProfileSummary = {
  name: "my-profile",
  model: "openhands/free-model",
  base_url: null,
  api_key_set: false,
};

function renderRow(profile: LlmProfileSummary = baseProfile) {
  return render(
    <ProfileRow
      profile={profile}
      isActive={false}
      onActivate={vi.fn()}
      onEdit={vi.fn()}
      onRename={vi.fn()}
      onDelete={vi.fn()}
      isActivating={false}
    />,
  );
}

describe("ProfileRow free-model label", () => {
  beforeEach(() => {
    mockState.hostedDomain = true;
    useFreeModelsStore.setState({
      freeModels: new Set(["openhands/free-model"]),
      defaultModel: null,
      defaultModelReady: true,
    });
  });

  it("appends the free label to a free model on a hosted domain", () => {
    renderRow();
    expect(screen.getByText("openhands/free-model (free)")).toBeInTheDocument();
  });

  it("does not append the free label to a paid model on a hosted domain", () => {
    renderRow({
      ...baseProfile,
      model: "openhands/paid-model",
    });
    expect(screen.getByText("openhands/paid-model")).toBeInTheDocument();
    expect(
      screen.queryByText("openhands/paid-model (free)"),
    ).not.toBeInTheDocument();
  });

  it("does not show any free-model label on a non-hosted domain", () => {
    mockState.hostedDomain = false;
    renderRow();
    expect(screen.getByText("openhands/free-model")).toBeInTheDocument();
    expect(
      screen.queryByText("openhands/free-model (free)"),
    ).not.toBeInTheDocument();
  });
});
