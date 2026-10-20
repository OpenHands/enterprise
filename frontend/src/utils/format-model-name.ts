/**
 * Suffix appended to a display name for a DB-flagged free OpenHands model.
 * English fallback; callers with a translator pass the localized suffix
 * (see `ProfileRow`).
 */
export const FREE_MODEL_SUFFIX = " (free)";

/**
 * Set of free ``provider/model`` ids. The frontend does not hardcode which
 * OpenHands models are free — the set is sourced from the backend model list
 * (DB-driven), the same channel that carries `verified`. See
 * {@link useFreeModels}. Callers that lack the set treat every model as paid.
 */
export type FreeModelSet = ReadonlySet<string>;

const EMPTY_FREE_MODELS: FreeModelSet = new Set<string>();

export const isFreeOpenHandsModel = (
  model: string | null | undefined,
  freeModels: FreeModelSet = EMPTY_FREE_MODELS,
): boolean => Boolean(model && freeModels.has(model));

export function formatModelNameForDisplay(
  model: string | null | undefined,
  freeModels: FreeModelSet = EMPTY_FREE_MODELS,
  freeSuffix: string = FREE_MODEL_SUFFIX,
): string | null {
  if (!model) return null;
  return isFreeOpenHandsModel(model, freeModels)
    ? `${model}${freeSuffix}`
    : model;
}
