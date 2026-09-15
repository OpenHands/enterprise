import { describe, it, expect } from "vitest";
import {
  parseMaxBudgetPerTask,
  extractSettings,
  isSettingsPageHidden,
} from "#/utils/settings-utils";
import { WebClientFeatureFlags } from "#/api/option-service/option.types";

const baseFlags = (
  overrides: Partial<WebClientFeatureFlags> = {},
): WebClientFeatureFlags => ({
  enable_billing: false,
  hide_llm_settings: false,
  enable_jira: false,
  enable_jira_dc: false,
  enable_linear: false,
  hide_users_page: false,
  hide_billing_page: false,
  hide_integrations_page: false,
  enable_onboarding: false,
  ...overrides,
});

describe("parseMaxBudgetPerTask", () => {
  it("should return null for empty string", () => {
    expect(parseMaxBudgetPerTask("")).toBeNull();
  });

  it("should return null for whitespace-only string", () => {
    expect(parseMaxBudgetPerTask("   ")).toBeNull();
  });

  it("should return null for non-numeric string", () => {
    expect(parseMaxBudgetPerTask("abc")).toBeNull();
  });

  it("should return null for values less than 1", () => {
    expect(parseMaxBudgetPerTask("0")).toBeNull();
    expect(parseMaxBudgetPerTask("0.5")).toBeNull();
    expect(parseMaxBudgetPerTask("-1")).toBeNull();
    expect(parseMaxBudgetPerTask("-10.5")).toBeNull();
  });

  it("should return the parsed value for valid numbers >= 1", () => {
    expect(parseMaxBudgetPerTask("1")).toBe(1);
    expect(parseMaxBudgetPerTask("1.0")).toBe(1);
    expect(parseMaxBudgetPerTask("1.5")).toBe(1.5);
    expect(parseMaxBudgetPerTask("10")).toBe(10);
    expect(parseMaxBudgetPerTask("100.99")).toBe(100.99);
  });

  it("should handle string numbers with leading/trailing whitespace", () => {
    expect(parseMaxBudgetPerTask("  1  ")).toBe(1);
    expect(parseMaxBudgetPerTask("  10.5  ")).toBe(10.5);
  });

  it("should return null for edge cases", () => {
    expect(parseMaxBudgetPerTask("0.999")).toBeNull();
    expect(parseMaxBudgetPerTask("NaN")).toBeNull();
    expect(parseMaxBudgetPerTask("Infinity")).toBeNull();
    expect(parseMaxBudgetPerTask("-Infinity")).toBeNull();
  });

  it("should handle scientific notation", () => {
    expect(parseMaxBudgetPerTask("1e0")).toBe(1);
    expect(parseMaxBudgetPerTask("1.5e1")).toBe(15);
    expect(parseMaxBudgetPerTask("5e-1")).toBeNull(); // 0.5, which is < 1
  });
});

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

describe("isSettingsPageHidden", () => {
  it("hides Integrations Hub when enable_integrations_hub is unset", () => {
    expect(
      isSettingsPageHidden("/settings/integrations-hub", baseFlags()),
    ).toBe(true);
    expect(
      isSettingsPageHidden(
        "/settings/integrations-hub/admin-catalog",
        baseFlags(),
      ),
    ).toBe(true);
  });

  it("hides Integrations Hub when enable_integrations_hub is false", () => {
    expect(
      isSettingsPageHidden(
        "/settings/integrations-hub",
        baseFlags({ enable_integrations_hub: false }),
      ),
    ).toBe(true);
  });

  it("shows Integrations Hub when enable_integrations_hub is true", () => {
    const flags = baseFlags({ enable_integrations_hub: true });
    expect(isSettingsPageHidden("/settings/integrations-hub", flags)).toBe(
      false,
    );
    expect(
      isSettingsPageHidden("/settings/integrations-hub/admin-catalog", flags),
    ).toBe(false);
  });

  it("does not hide the existing Integrations settings page via the Hub flag", () => {
    expect(isSettingsPageHidden("/settings/integrations", baseFlags())).toBe(
      false,
    );
  });

  it("hides personal Integrations nested pages when hide_integrations_page is true", () => {
    const flags = baseFlags({ hide_integrations_page: true });
    expect(isSettingsPageHidden("/settings/integrations", flags)).toBe(true);
    expect(
      isSettingsPageHidden("/settings/integrations/agent-requests", flags),
    ).toBe(true);
  });

  it("does not treat Integrations Hub as the personal Integrations page", () => {
    const flags = baseFlags({
      hide_integrations_page: true,
      enable_integrations_hub: true,
    });
    expect(isSettingsPageHidden("/settings/integrations-hub", flags)).toBe(
      false,
    );
    expect(
      isSettingsPageHidden("/settings/integrations-hub/admin-catalog", flags),
    ).toBe(false);
  });
});
