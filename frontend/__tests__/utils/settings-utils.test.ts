import { describe, it, expect } from "vitest";
import { extractSettings, isSettingsPageHidden } from "#/utils/settings-utils";
import { WebClientFeatureFlags } from "#/api/option-service/option.types";

describe("extractSettings", () => {
  it("should preserve model name case when extracting settings", () => {
    const testCases = [
      { provider: "sambanova", model: "Meta-Llama-3.1-8B-Instruct" },
      { provider: "openai", model: "GPT-4o" },
      { provider: "anthropic", model: "Claude-3-5-Sonnet" },
      { provider: "openrouter", model: "CamelCaseModel" },
    ];

    testCases.forEach(({ provider, model }) => {
      const formData = new FormData();
      formData.set("llm-provider-input", provider);
      formData.set("llm-model-input", model);

      const settings = extractSettings(formData);

      const expectedModel = `${provider}/${model}`;
      const as = settings.agent_settings_diff as Record<string, unknown>;
      const llm = as?.llm as Record<string, unknown>;
      expect(llm?.model).toBe(expectedModel);
      if (expectedModel !== expectedModel.toLowerCase()) {
        expect(llm?.model).not.toBe(expectedModel.toLowerCase());
      }
    });
  });

  it("should preserve selected model case and ignore unsupported custom-model inputs", () => {
    const formData = new FormData();
    formData.set("llm-provider-input", "sambanova");
    formData.set("llm-model-input", "Meta-Llama-3.1-8B-Instruct");
    formData.set("use-advanced-options", "true");
    formData.set("custom-model", "Custom-Model-Name");

    const settings = extractSettings(formData);

    const as = settings.agent_settings_diff as Record<string, unknown>;
    const llm = as?.llm as Record<string, unknown>;
    expect(llm?.model).toBe("sambanova/Meta-Llama-3.1-8B-Instruct");
    expect(llm?.model).not.toBe("custom-model-name");
  });
});

describe("isSettingsPageHidden - service accounts", () => {
  const path = "/settings/service-accounts";

  it("hides the page when the feature flags are missing", () => {
    expect(isSettingsPageHidden(path, undefined)).toBe(true);
  });

  it("hides the page when enable_service_accounts is missing or false", () => {
    expect(isSettingsPageHidden(path, {} as WebClientFeatureFlags)).toBe(true);
    expect(
      isSettingsPageHidden(path, {
        enable_service_accounts: false,
      } as WebClientFeatureFlags),
    ).toBe(true);
  });

  it("shows the page when enable_service_accounts is true", () => {
    expect(
      isSettingsPageHidden(path, {
        enable_service_accounts: true,
      } as WebClientFeatureFlags),
    ).toBe(false);
  });
});
