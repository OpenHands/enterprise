import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import OptionService from "#/api/option-service/option-service.api";
import { ModelSelector } from "#/components/shared/modals/settings/model-selector";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
import type {
  LLMProvider,
  LLMModel,
} from "#/api/config-service/config-service.types";

const mockProviders: LLMProvider[] = [
  { name: "openai", verified: true },
  { name: "azure", verified: false },
  { name: "vertex_ai", verified: false },
  // Provider used by the free-model UI tests (DB-driven free flags).
  { name: "openhands", verified: true },
  // Provider whose model list comes back empty (no entry in
  // mockModelsByProvider) — used by the unavailable-model warning tests.
  { name: "groq", verified: false },
];

const mockModelsByProvider: Record<string, LLMModel[]> = {
  openai: [
    { provider: "openai", name: "gpt-4o", verified: true },
    { provider: "openai", name: "gpt-4o-mini", verified: true },
  ],
  azure: [
    { provider: "azure", name: "ada", verified: false },
    { provider: "azure", name: "gpt-35-turbo", verified: false },
    // Served-but-not-promoted alias route (e.g. a legacy name a managed
    // proxy keeps serving after a rename): never a dropdown option, but a
    // saved setting referencing it still counts as available.
    { provider: "azure", name: "legacy-alias", verified: false, hidden: true },
  ],
  vertex_ai: [
    { provider: "vertex_ai", name: "chat-bison", verified: false },
    { provider: "vertex_ai", name: "chat-bison-32k", verified: false },
  ],
  openhands: [
    { provider: "openhands", name: "free-model", verified: true, free: true },
    { provider: "openhands", name: "paid-model", verified: true, free: false },
  ],
};

const mockState = vi.hoisted(() => ({ hostedDomain: true }));

vi.mock("#/hooks/query/use-search-providers", () => ({
  useSearchProviders: () => ({ data: mockProviders }),
}));

vi.mock("#/hooks/query/use-provider-models", () => ({
  useProviderModels: (provider: string | null) => ({
    data: provider ? (mockModelsByProvider[provider] ?? []) : [],
    isLoading: false,
    error: null,
  }),
}));

vi.mock("#/utils/domain-gate", () => ({
  isOpenHandsHostedDomain: () => mockState.hostedDomain,
}));

vi.mock("#/hooks/use-app-mode", () => ({
  useAppMode: () => ({ isEnterpriseCloud: false }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        LLM$PROVIDER: "LLM Provider",
        LLM$MODEL: "LLM Model",
        LLM$SELECT_PROVIDER_PLACEHOLDER: "Select a provider",
        LLM$SELECT_MODEL_PLACEHOLDER: "Select a model",
      };
      return translations[key] || key;
    },
  }),
}));

function renderWithQuery(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("ModelSelector without a LiteLLM gateway", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("labels the openhands provider OpenHands Cloud", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        feature_flags: {
          ...createMockWebClientConfig().feature_flags,
          enable_litellm: false,
        },
      }),
    );
    const user = userEvent.setup();
    renderWithQuery(<ModelSelector />);

    await user.click(screen.getByLabelText("LLM Provider"));

    expect(await screen.findByText("OpenHands Cloud")).toBeInTheDocument();
    expect(screen.queryByText("OpenHands")).not.toBeInTheDocument();
  });
});

describe("ModelSelector", () => {
  it("should display the provider selector", async () => {
    const user = userEvent.setup();
    renderWithQuery(<ModelSelector />);

    const selector = screen.getByLabelText("LLM Provider");
    expect(selector).toBeInTheDocument();

    await user.click(selector);

    expect(screen.getByText("OpenAI")).toBeInTheDocument();
    expect(screen.getByText("Azure")).toBeInTheDocument();
    expect(screen.getByText("VertexAI")).toBeInTheDocument();
  });

  it("should disable the model selector if the provider is not selected", async () => {
    const user = userEvent.setup();
    renderWithQuery(<ModelSelector />);

    const modelSelector = screen.getByLabelText("LLM Model");
    expect(modelSelector).toBeDisabled();

    const providerSelector = screen.getByLabelText("LLM Provider");
    await user.click(providerSelector);

    const vertexAI = screen.getByText("VertexAI");
    await user.click(vertexAI);

    expect(modelSelector).not.toBeDisabled();
  });

  it("should display the model selector", async () => {
    const user = userEvent.setup();
    renderWithQuery(<ModelSelector />);

    const providerSelector = screen.getByLabelText("LLM Provider");
    await user.click(providerSelector);

    const azureProvider = screen.getByText("Azure");
    await user.click(azureProvider);

    const modelSelector = screen.getByLabelText("LLM Model");
    await user.click(modelSelector);

    expect(screen.getByText("ada")).toBeInTheDocument();
    expect(screen.getByText("gpt-35-turbo")).toBeInTheDocument();
    // Hidden alias routes are served by the backend but never promoted.
    expect(screen.queryByText("legacy-alias")).not.toBeInTheDocument();
  });

  it("should call onChange when the provider and model change", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    renderWithQuery(<ModelSelector onChange={onChange} />);

    const providerSelector = screen.getByLabelText("LLM Provider");
    await user.click(providerSelector);
    await user.click(screen.getByText("Azure"));

    const modelSelector = screen.getByLabelText("LLM Model");
    await user.click(modelSelector);
    await user.click(screen.getByText("ada"));

    expect(onChange).toHaveBeenNthCalledWith(1, "azure", null);
    expect(onChange).toHaveBeenNthCalledWith(2, "azure", "ada");
  });

  it("should have a default value if passed", async () => {
    renderWithQuery(<ModelSelector currentModel="azure/ada" />);

    await waitFor(() => {
      expect(screen.getByLabelText("LLM Provider")).toHaveValue("Azure");
      expect(screen.getByLabelText("LLM Model")).toHaveValue("ada");
    });
  });

  it("should stretch the provider and model selectors across the full row", () => {
    const { container } = renderWithQuery(<ModelSelector />);

    expect(container.firstChild).toHaveClass("w-full");
  });

  describe("unavailable model warning", () => {
    it("shows a warning when the saved model is missing from a non-empty model list", async () => {
      renderWithQuery(<ModelSelector currentModel="azure/removed-model" />);

      await waitFor(() => {
        expect(
          screen.getByTestId("model-unavailable-warning"),
        ).toBeInTheDocument();
      });
    });

    it("does not show a warning when the saved model matches a hidden alias", async () => {
      // "legacy-alias" is served by the backend (hidden=true) but is not a
      // dropdown option — the saved setting still routes, so no warning.
      renderWithQuery(<ModelSelector currentModel="azure/legacy-alias" />);

      await waitFor(() => {
        expect(screen.getByLabelText("LLM Provider")).toHaveValue("Azure");
      });
      expect(
        screen.queryByTestId("model-unavailable-warning"),
      ).not.toBeInTheDocument();
    });

    it("does not show a warning when the saved model is in the model list", async () => {
      renderWithQuery(<ModelSelector currentModel="azure/ada" />);

      await waitFor(() => {
        expect(screen.getByLabelText("LLM Model")).toHaveValue("ada");
      });
      expect(
        screen.queryByTestId("model-unavailable-warning"),
      ).not.toBeInTheDocument();
    });

    it("does not show a warning when the provider's model list is empty", async () => {
      // "groq" has no entry in mockModelsByProvider, so the hook returns []
      // — an empty list must not cast doubt on the saved model.
      renderWithQuery(<ModelSelector currentModel="groq/llama3-70b" />);

      await waitFor(() => {
        expect(screen.getByLabelText("LLM Provider")).toHaveValue("Groq");
      });
      expect(
        screen.queryByTestId("model-unavailable-warning"),
      ).not.toBeInTheDocument();
    });

    it("clears the warning when the user picks an available model", async () => {
      const user = userEvent.setup();
      renderWithQuery(<ModelSelector currentModel="azure/removed-model" />);

      await waitFor(() => {
        expect(
          screen.getByTestId("model-unavailable-warning"),
        ).toBeInTheDocument();
      });

      const modelSelector = screen.getByLabelText("LLM Model");
      await user.click(modelSelector);
      await user.click(screen.getByText("ada"));

      await waitFor(() => {
        expect(
          screen.queryByTestId("model-unavailable-warning"),
        ).not.toBeInTheDocument();
      });
    });
  });

  describe("free-model UI", () => {
    const BADGE = "SETTINGS$FREE_MODEL_BADGE";

    beforeEach(() => {
      mockState.hostedDomain = true;
      mockModelsByProvider.openhands = [
        {
          provider: "openhands",
          name: "free-model",
          verified: true,
          free: true,
        },
        {
          provider: "openhands",
          name: "paid-model",
          verified: true,
          free: false,
        },
      ];
    });

    it("shows the Free badge only on free options when the list is open", async () => {
      const user = userEvent.setup();
      renderWithQuery(<ModelSelector currentModel="openhands/free-model" />);

      const modelSelector = await screen.findByLabelText("LLM Model");
      await user.click(modelSelector);

      const freeOption = await screen.findByRole("option", {
        name: /free-model/,
      });
      expect(freeOption).toHaveTextContent(BADGE);

      const paidOption = screen.getByRole("option", { name: /paid-model/ });
      expect(paidOption).not.toHaveTextContent(BADGE);
    });

    it("renders the free-models note when the openhands provider has free models on a hosted domain", async () => {
      renderWithQuery(<ModelSelector currentModel="openhands/free-model" />);

      await waitFor(() => {
        expect(
          screen.getByTestId("openhands-free-models-note"),
        ).toBeInTheDocument();
      });
    });

    it("renders the selected-model Free badge for a selected free model on a hosted domain", async () => {
      renderWithQuery(<ModelSelector currentModel="openhands/free-model" />);

      await waitFor(() => {
        expect(
          screen.getByTestId("selected-free-model-badge"),
        ).toBeInTheDocument();
      });
      expect(screen.getByTestId("selected-free-model-badge")).toHaveTextContent(
        BADGE,
      );
    });

    it("hides the selected-model Free badge while the user is typing a search", async () => {
      const user = userEvent.setup();
      renderWithQuery(<ModelSelector currentModel="openhands/free-model" />);

      const modelSelector = await screen.findByLabelText("LLM Model");
      await waitFor(() => {
        expect(
          screen.getByTestId("selected-free-model-badge"),
        ).toBeInTheDocument();
      });

      await user.click(modelSelector);
      await user.clear(modelSelector);
      await user.type(modelSelector, "paid");

      expect(screen.getByLabelText("LLM Model")).toHaveValue("paid");
      expect(
        screen.queryByTestId("selected-free-model-badge"),
      ).not.toBeInTheDocument();
    });

    it("does not render any free-model UI on a non-hosted domain", async () => {
      mockState.hostedDomain = false;
      renderWithQuery(<ModelSelector currentModel="openhands/free-model" />);

      await waitFor(() => {
        expect(screen.getByLabelText("LLM Model")).toHaveValue("free-model");
      });
      expect(
        screen.queryByTestId("openhands-free-models-note"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("selected-free-model-badge"),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(BADGE)).not.toBeInTheDocument();
    });

    it("does not render the note when the openhands provider has no free models", async () => {
      mockModelsByProvider.openhands = [
        {
          provider: "openhands",
          name: "paid-model",
          verified: true,
          free: false,
        },
      ];
      renderWithQuery(<ModelSelector currentModel="openhands/paid-model" />);

      await waitFor(() => {
        expect(screen.getByLabelText("LLM Model")).toHaveValue("paid-model");
      });
      expect(
        screen.queryByTestId("openhands-free-models-note"),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(BADGE)).not.toBeInTheDocument();
    });
  });
});
