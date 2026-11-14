import { openHands } from "../open-hands-axios";

export interface LlmProfileSummary {
  name: string;
  model: string | null;
  base_url: string | null;
  api_key_set: boolean;
  /** Shared provider connection id this profile is linked to, if any. */
  provider_connection_id?: string | null;
}

// Not exported — only `listProfiles` reads it as its response shape.
interface LlmProfileListResponse {
  profiles: LlmProfileSummary[];
  active_profile: string | null;
}

export interface SaveLlmProfileRequest {
  include_secrets?: boolean;
  // True when the user typed no new key, so the backend keeps the profile's
  // stored key instead of snapshotting the active settings' key.
  preserve_existing_api_key?: boolean;
  llm?: {
    model: string;
    base_url?: string | null;
    api_key?: string | null;
  } & Record<string, unknown>;
}

export interface ValidateLlmProfileRequest {
  /** Merged over the stored LLM settings, like `agent_settings_diff.llm`. */
  llm: Record<string, unknown>;
  preserve_existing_api_key?: boolean;
}

export interface ValidateLlmProfileResponse {
  valid: boolean;
  error?: { type: string; message: string } | null;
}

// Above the backend's own 15 s bound on the provider call.
const VALIDATE_TIMEOUT_MS = 30000;

class ProfilesService {
  static async listProfiles(): Promise<LlmProfileListResponse> {
    const { data } = await openHands.get<LlmProfileListResponse>(
      "/api/v1/settings/profiles",
    );
    return data;
  }

  static async saveProfile(
    name: string,
    request: SaveLlmProfileRequest = {},
  ): Promise<void> {
    await openHands.post(
      `/api/v1/settings/profiles/${encodeURIComponent(name)}`,
      request,
    );
  }

  /**
   * Pre-save connection check. `null` means "no verdict" (the draft is not
   * checked by this server, the check limit was hit, or the request failed),
   * so callers save as usual; only a `valid: false` verdict blocks a save.
   */
  static async validateProfile(
    name: string,
    request: ValidateLlmProfileRequest,
  ): Promise<ValidateLlmProfileResponse | null> {
    try {
      const { data } = await openHands.post<ValidateLlmProfileResponse>(
        `/api/v1/settings/profiles/${encodeURIComponent(name)}/validate`,
        request,
        { timeout: VALIDATE_TIMEOUT_MS },
      );
      return data;
    } catch {
      return null;
    }
  }

  static async deleteProfile(name: string): Promise<void> {
    await openHands.delete(
      `/api/v1/settings/profiles/${encodeURIComponent(name)}`,
    );
  }

  static async activateProfile(name: string): Promise<void> {
    await openHands.post(
      `/api/v1/settings/profiles/${encodeURIComponent(name)}/activate`,
    );
  }

  static async renameProfile(name: string, newName: string): Promise<void> {
    await openHands.post(
      `/api/v1/settings/profiles/${encodeURIComponent(name)}/rename`,
      { new_name: newName },
    );
  }
}

export default ProfilesService;
