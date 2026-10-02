export const FREE_MODEL_BADGE_LABEL = "Free";

/** Suffix appended to a display name for a DB-flagged free OpenHands model. */
export const FREE_MODEL_SUFFIX = " (free)";

/**
 * Static pretty-print labels for OpenHands models known at build time. Free
 * status still comes only from the backend-provided `freeModels` set.
 */
export const FREE_OPENHANDS_MODELS = {
  "openhands/deepseek-v4-flash": "OpenHands DeepSeek V4 Flash",
} as const;

export const FREE_OPENHANDS_MODEL_IDS = Object.keys(FREE_OPENHANDS_MODELS);

/**
 * Set of free ``provider/model`` ids. The frontend does not hardcode which
 * OpenHands models are free — the set is sourced from the backend model list
 * (DB-driven), the same channel that carries `verified`. See
 * {@link useFreeModels}. Callers that lack the set treat every model as paid.
 */
export type FreeModelSet = ReadonlySet<string>;

const EMPTY_FREE_MODELS: FreeModelSet = new Set<string>();

/**
 * Whether a model id routes through the OpenHands provider (the `openhands/`
 * prefix). On cloud the OpenHands provider is backed by a server-minted LLM
 * key rather than a user-supplied one, so callers use this to hide the inline
 * API key / base URL inputs and strip those fields from the save payload.
 */
export const isOpenHandsProviderModel = (
  model: string | null | undefined,
): boolean => Boolean(model?.startsWith("openhands/"));

export const isFreeOpenHandsModel = (
  model: string | null | undefined,
  freeModels: FreeModelSet = EMPTY_FREE_MODELS,
): boolean => Boolean(model && freeModels.has(model));

function appendFreeSuffix(display: string, isFree: boolean): string {
  return isFree ? `${display}${FREE_MODEL_SUFFIX}` : display;
}

export function formatModelNameForDisplay(
  model: string | null | undefined,
  freeModels: FreeModelSet = EMPTY_FREE_MODELS,
): string | null {
  if (!model) return null;
  return appendFreeSuffix(model, isFreeOpenHandsModel(model, freeModels));
}

export function formatProviderModelNameForDisplay(
  provider: string | null | undefined,
  model: string | null | undefined,
  freeModels: FreeModelSet = EMPTY_FREE_MODELS,
): string | null {
  if (!model) return null;
  const fullModel = provider ? `${provider}/${model}` : model;
  return appendFreeSuffix(model, isFreeOpenHandsModel(fullModel, freeModels));
}
