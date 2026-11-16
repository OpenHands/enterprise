/** V1 Config API types for models and providers */

export interface LLMModel {
  provider: string | null;
  name: string;
  verified: boolean;
  /** Served but not promoted (e.g. a legacy alias route on a managed
   *  LiteLLM proxy): never offered as a dropdown option, yet a saved
   *  setting that references it still counts as available. Optional so
   *  older backends (no field) behave as before. */
  hidden?: boolean;
  /** Bare name of the visible model a hidden alias routes to (same
   *  provider); mirrors the backend LLMModelInfo.canonical contract. */
  canonical?: string;
  /**
   * Whether the model is free to use on the OpenHands provider. Mirrors
   * `verified`: populated by the backend model list (DB-driven on cloud)
   * and defaults to `false` where no free metadata exists. Optional so
   * older backends (no field) behave as before.
   */
  free?: boolean;
  /**
   * Whether this is the provider's default model. Mirrors `free`: DB-driven
   * on cloud, absent where no default metadata exists. Used to preselect the
   * model on onboarding and when creating a new model for the provider.
   */
  default?: boolean;
}

export interface LLMModelPage {
  items: LLMModel[];
  next_page_id: string | null;
}

export interface SearchModelsParams {
  page_id?: string;
  limit?: number;
  query?: string;
  verified__eq?: boolean;
  provider__eq?: string;
}

export interface LLMProvider {
  name: string;
  verified: boolean;
}

export interface ProviderPage {
  items: LLMProvider[];
  next_page_id: string | null;
}

export interface SearchProvidersParams {
  page_id?: string;
  limit?: number;
  query?: string;
  verified__eq?: boolean;
}
