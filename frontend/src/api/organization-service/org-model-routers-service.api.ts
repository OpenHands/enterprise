import { openHands } from "../open-hands-axios";

/**
 * Organization Model Routers (SDK meta-profiles): a classifier LLM profile plus
 * a direct-routing prompt that picks the saved LLM profile a task should run
 * on. Every model reference is the *name* of an org LLM profile.
 *
 * Org-scoped routes live at `/api/organizations/{orgId}/meta-profiles` (see
 * `server/routes/org_meta_profiles.py`). Listing requires `VIEW_ORG_SETTINGS`;
 * save/delete/activate/deactivate require `EDIT_ORG_SETTINGS`. The whole
 * surface answers 404 while the `enable_model_router` feature flag is off.
 */
export interface MetaProfileClass {
  description: string;
  /** Name of the saved LLM profile to switch to for this class. */
  model: string;
}

export interface MetaProfile {
  /** Name of the saved LLM profile used to classify the task. */
  classifier_model: string;
  /** Structured mode targets; empty for direct-prompt routers. */
  classes?: MetaProfileClass[];
  /** Direct-routing prompt; must include `{{ instance_text }}`. */
  prompt_template?: string | null;
  /** Text inserted into `{{ model_table }}` of the prompt. */
  model_table?: string | null;
}

export interface MetaProfileInfo {
  name: string;
  classifier_model: string | null;
  num_classes: number;
}

export interface MetaProfileListResponse {
  meta_profiles: MetaProfileInfo[];
  active_meta_profile: string | null;
  /** Org-wide "Run on first message" toggle. */
  run_at_conversation_start: boolean;
}

export interface MetaProfileDetailResponse {
  name: string;
  config: MetaProfile;
}

export interface MetaProfileMutationResponse {
  name: string;
  message: string;
}

export interface UpdateModelRouterSettingsRequest {
  run_at_conversation_start: boolean;
}

const basePath = (orgId: string) => `/api/organizations/${orgId}/meta-profiles`;

class OrgModelRoutersService {
  static async list(orgId: string): Promise<MetaProfileListResponse> {
    const { data } = await openHands.get<MetaProfileListResponse>(
      basePath(orgId),
    );
    return data;
  }

  static async get(
    orgId: string,
    name: string,
  ): Promise<MetaProfileDetailResponse> {
    const { data } = await openHands.get<MetaProfileDetailResponse>(
      `${basePath(orgId)}/${encodeURIComponent(name)}`,
    );
    return data;
  }

  static async save(
    orgId: string,
    name: string,
    config: MetaProfile,
  ): Promise<MetaProfileMutationResponse> {
    const { data } = await openHands.post<MetaProfileMutationResponse>(
      `${basePath(orgId)}/${encodeURIComponent(name)}`,
      config,
    );
    return data;
  }

  static async delete(
    orgId: string,
    name: string,
  ): Promise<MetaProfileMutationResponse> {
    const { data } = await openHands.delete<MetaProfileMutationResponse>(
      `${basePath(orgId)}/${encodeURIComponent(name)}`,
    );
    return data;
  }

  static async activate(
    orgId: string,
    name: string,
  ): Promise<MetaProfileMutationResponse> {
    const { data } = await openHands.post<MetaProfileMutationResponse>(
      `${basePath(orgId)}/${encodeURIComponent(name)}/activate`,
      {},
    );
    return data;
  }

  static async deactivate(
    orgId: string,
    name: string,
  ): Promise<MetaProfileMutationResponse> {
    const { data } = await openHands.post<MetaProfileMutationResponse>(
      `${basePath(orgId)}/${encodeURIComponent(name)}/deactivate`,
      {},
    );
    return data;
  }

  static async updateSettings(
    orgId: string,
    request: UpdateModelRouterSettingsRequest,
  ): Promise<MetaProfileListResponse> {
    const { data } = await openHands.patch<MetaProfileListResponse>(
      basePath(orgId),
      request,
    );
    return data;
  }
}

export default OrgModelRoutersService;
