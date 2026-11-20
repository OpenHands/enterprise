import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { SwitchAcpModelButton } from "#/components/features/chat/switch-acp-model-button";
import type { V1AppConversation } from "#/api/conversation-service/v1-conversation-service.types";

// `useTranslation` is mocked globally in vitest.setup.ts.

const mockUseActiveConversation = vi.hoisted(() => vi.fn());
const mockSwitchModel = vi.hoisted(() => vi.fn());
const mockSaveSettings = vi.hoisted(() => vi.fn());

vi.mock("#/hooks/query/use-active-conversation", () => ({
  useActiveConversation: () => mockUseActiveConversation(),
}));

vi.mock("#/hooks/use-conversation-id", () => ({
  useConversationId: () => ({ conversationId: "conv-1" }),
}));

vi.mock("#/hooks/mutation/use-switch-acp-model", () => ({
  useSwitchAcpModel: () => ({ mutate: mockSwitchModel, isPending: false }),
}));

vi.mock("#/hooks/mutation/use-save-settings", () => ({
  useSaveSettings: () => ({ mutate: mockSaveSettings, isPending: false }),
}));

// What the conversation's ACP session reported it offers.
const SESSION_MODELS = [
  { model_id: "gpt-5.6-sol", name: "GPT-5.6 Sol" },
  { model_id: "gpt-6-astra", name: null },
];

const conversation = (
  overrides: Partial<V1AppConversation>,
): V1AppConversation =>
  ({
    id: "conv-1",
    agent_kind: "acp",
    acp_server: "codex",
    llm_model: "gpt-5.6-sol",
    acp_available_models: SESSION_MODELS,
    acp_supports_runtime_model_switch: true,
    sandbox_status: "RUNNING",
    ...overrides,
  }) as unknown as V1AppConversation;

const renderButton = (data: V1AppConversation) => {
  mockUseActiveConversation.mockReturnValue({ data });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <SwitchAcpModelButton />
      </QueryClientProvider>
    </MemoryRouter>,
  );
};

describe("SwitchAcpModelButton", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("labels the button with the name the session reported for the current model", () => {
    renderButton(conversation({}));

    expect(screen.getByTestId("switch-acp-model-button")).toHaveTextContent(
      "GPT-5.6 Sol",
    );
  });

  it("lists the reported models and switches to the chosen one", async () => {
    renderButton(conversation({}));

    await userEvent.click(screen.getByTestId("switch-acp-model-button"));

    expect(
      screen.getByTestId("switch-acp-model-option-gpt-5.6-sol"),
    ).toBeInTheDocument();
    // A model the session did not name shows its id.
    expect(
      screen.getByTestId("switch-acp-model-option-gpt-6-astra"),
    ).toHaveTextContent("gpt-6-astra");

    await userEvent.click(
      screen.getByTestId("switch-acp-model-option-gpt-6-astra"),
    );

    expect(mockSwitchModel).toHaveBeenCalledWith(
      { conversationId: "conv-1", model: "gpt-6-astra" },
      expect.anything(),
    );
  });

  it("shows the raw id when the current model is not in the reported list", () => {
    renderButton(conversation({ llm_model: "custom-model" }));

    expect(screen.getByTestId("switch-acp-model-button")).toHaveTextContent(
      "custom-model",
    );
  });

  it("renders nothing until the session has reported its models", () => {
    renderButton(conversation({ acp_available_models: [] }));

    expect(
      screen.queryByTestId("switch-acp-model-button"),
    ).not.toBeInTheDocument();
  });

  it("renders nothing when the session cannot switch models mid-conversation", () => {
    renderButton(conversation({ acp_supports_runtime_model_switch: false }));

    expect(
      screen.queryByTestId("switch-acp-model-button"),
    ).not.toBeInTheDocument();
  });

  it("renders nothing for OpenHands conversations", () => {
    renderButton(conversation({ agent_kind: "openhands", acp_server: null }));

    expect(
      screen.queryByTestId("switch-acp-model-button"),
    ).not.toBeInTheDocument();
  });
});
