import type { ACPProviderConfig } from "#/api/option-service/option.types";
import { tokenizeCommand } from "#/utils/shell-tokenize";

/** Preset key for a hand-written ACP command that matches no provider. */
export const CUSTOM_PRESET = "custom";
/** Dropdown key for a model that is not among the provider's suggestions. */
export const CUSTOM_MODEL_KEY = "__custom__";

export function toStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string")
    : [];
}

/** The provider whose default command the typed command equals, else custom. */
export function detectPreset(
  commandText: string,
  providers: ACPProviderConfig[],
): string {
  const normalized = tokenizeCommand(commandText).join(" ");
  for (const provider of providers) {
    if (normalized === provider.default_command.join(" ")) {
      return provider.key;
    }
  }
  return CUSTOM_PRESET;
}

export function isKnownModel(
  provider: ACPProviderConfig | undefined,
  model: string,
): boolean {
  return (
    provider?.available_models?.some(({ id }) => id === model.trim()) ?? false
  );
}
