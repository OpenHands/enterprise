/* eslint-disable i18next/no-literal-string */
import React from "react";

interface FeatureDisabledScreenProps {
  /** Short, feature-specific heading, e.g. "Budgets". */
  title: string;
  /** Optional extra detail appended after the standard LiteLLM message. */
  description?: string;
  testId?: string;
}

/**
 * Placeholder shown in place of a LiteLLM-dependent feature (Budgets,
 * managed/OpenHands models, managed LLM keys, ...) when the deployment-wide
 * ``enable_litellm`` feature flag is off. Mirrors the plain, hardcoded-copy
 * style already used by ``Budgets`` for admin-facing states (loading/empty)
 * rather than introducing new i18n keys for an enterprise-only screen.
 */
export function FeatureDisabledScreen({
  title,
  description,
  testId = "feature-disabled-screen",
}: FeatureDisabledScreenProps) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2 py-16 text-center"
      data-testid={testId}
    >
      <h2 className="text-lg font-medium text-foreground">{title}</h2>
      <p className="text-muted max-w-md">
        Please enable LiteLLM to use this feature.
      </p>
      {description && <p className="text-muted max-w-md">{description}</p>}
    </div>
  );
}
