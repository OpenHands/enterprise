import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModelSelector } from "./model-selector";

const mockState = vi.hoisted(() => ({
  hostedDomain: true,
  providers: [{ name: "openhands", verified: true }],
  models: [
    { provider: "openhands", name: "free-model", verified: true, free: true },
    { provider: "openhands", name: "paid-model", verified: true, free: false },
  ],
}));

vi.mock("#/hooks/query/use-search-providers", () => ({
  useSearchProviders: () => ({ data: mockState.providers }),
}));

vi.mock("#/hooks/query/use-provider-models", () => ({
  useProviderModels: () => ({
    data: mockState.models,
    isLoading: false,
    error: null,
  }),
}));

vi.mock("#/hooks/use-app-mode", () => ({
  useAppMode: () => ({ isEnterpriseCloud: false }),
}));

vi.mock("#/utils/domain-gate", () => ({
  isOpenHandsHostedDomain: () => mockState.hostedDomain,
}));

function renderSelector(currentModel = "openhands/free-model") {
  return render(<ModelSelector currentModel={currentModel} />);
}

describe("ModelSelector free-model UI", () => {
  beforeEach(() => {
    mockState.hostedDomain = true;
    mockState.providers = [{ name: "openhands", verified: true }];
    mockState.models = [
      { provider: "openhands", name: "free-model", verified: true, free: true },
      {
        provider: "openhands",
        name: "paid-model",
        verified: true,
        free: false,
      },
    ];
  });

  it("renders the Free badge on free models on a hosted domain", () => {
    renderSelector();
    const badges = screen.getAllByText("Free");
    expect(badges.length).toBeGreaterThan(0);
  });

  it("renders the free-models info note when the openhands provider has free models on a hosted domain", () => {
    renderSelector();
    expect(
      screen.getByTestId("openhands-free-models-note"),
    ).toBeInTheDocument();
  });

  it("renders the selected-free-model badge for a selected free model on a hosted domain", () => {
    renderSelector();
    expect(screen.getByTestId("selected-free-model-badge")).toBeInTheDocument();
  });

  it("does not render any free-model UI on a non-hosted domain", () => {
    mockState.hostedDomain = false;
    renderSelector();
    expect(
      screen.queryByTestId("openhands-free-models-note"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("selected-free-model-badge"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Free")).not.toBeInTheDocument();
  });

  it("does not render the note when the openhands provider has no free models", () => {
    mockState.models = [
      {
        provider: "openhands",
        name: "paid-model",
        verified: true,
        free: false,
      },
    ];
    renderSelector("openhands/paid-model");
    expect(
      screen.queryByTestId("openhands-free-models-note"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Free")).not.toBeInTheDocument();
  });
});
