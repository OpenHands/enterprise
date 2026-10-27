import { openHands } from "../open-hands-axios";

/**
 * Which collection a profile name addresses on ``/api/agent-profiles``:
 * ``personal`` is the member's own (``org_member.agent_profiles``), ``org``
 * the organization's shared, admin-managed one. Omitting the parameter means
 * ``org`` server-side, so every call here passes it explicitly.
 */
export type AgentProfileScope = "personal" | "org";

export type AgentKind = "openhands" | "acp";

export interface AgentProfileSummary {
  id: string | null;
  name: string;
  agent_kind: AgentKind;
  revision: number | null;
  llm_profile_ref: string | null;
  mcp_server_refs: string[] | null;
  scope: AgentProfileScope;
}

export interface AgentProfileListResponse {
  profiles: AgentProfileSummary[];
  active_agent_profile_id: string | null;
}

// Mirrors the SDK ``AgentProfile`` union the backend validates (openhands-sdk
// 1.58.1, profile schema v4). Only the fields this editor reads or writes are typed precisely;
// the rest ride along through ``mergeAgentProfileSaveInput`` untouched.
interface AgentProfileBase {
  schema_version?: number;
  id: string;
  name: string;
  revision: number;
  /** ``null`` = every configured MCP server, ``[]`` = none. */
  mcp_server_refs?: string[] | null;
  /** ``null`` = no restriction on the member's saved secrets. */
  secret_refs?: string[] | null;
}

/** One entry of a profile's pinned tool list (SDK ``Tool`` spec). */
export interface AgentProfileTool {
  name: string;
  params?: Record<string, unknown>;
}

export interface OpenHandsAgentProfile extends AgentProfileBase {
  agent_kind: "openhands";
  llm_profile_ref: string;
  agent?: string;
  /** ``null``/absent = the SDK standard tool set; a list pins the tools.
   *  Sub-agents (``task_tool_set``) and LLM switching (``switch_llm``) are
   *  entries here -- the SDK retired their boolean switches. */
  tools?: AgentProfileTool[] | null;
  persona?: string | null;
  system_message_suffix?: string | null;
  disabled_skills?: string[];
  public_skills?: string[] | null;
  condenser?: Record<string, unknown>;
  verification?: Record<string, unknown>;
  enable_classify_and_switch_llm_tool?: boolean;
  meta_profile_ref?: string | null;
  tool_concurrency_limit?: number;
}

export interface ACPAgentProfile extends AgentProfileBase {
  agent_kind: "acp";
  acp_server: string;
  acp_model?: string | null;
  acp_session_mode?: string | null;
  acp_prompt_timeout?: number;
  acp_startup_timeout?: number;
  /** Shell string, unlike ``agent_settings.acp_command`` (a token array). */
  acp_command?: string | null;
  acp_args?: string[] | null;
}

export type AgentProfile = OpenHandsAgentProfile | ACPAgentProfile;

/** Save body: the path name is authoritative and the server owns id/revision. */
export type AgentProfileSaveInput =
  | Omit<OpenHandsAgentProfile, "id" | "name" | "revision">
  | Omit<ACPAgentProfile, "id" | "name" | "revision">;

export interface AgentProfileDetailResponse {
  name: string;
  profile: AgentProfile;
  scope: AgentProfileScope;
}

const profileUrl = (name: string) =>
  `/api/agent-profiles/${encodeURIComponent(name)}`;

class AgentProfilesService {
  /** Both scopes, each summary tagged with its ``scope``. */
  static async listProfiles(): Promise<AgentProfileListResponse> {
    const { data } = await openHands.get<AgentProfileListResponse>(
      "/api/agent-profiles",
    );
    return data;
  }

  static async getProfile(
    name: string,
    scope: AgentProfileScope,
  ): Promise<AgentProfileDetailResponse> {
    const { data } = await openHands.get<AgentProfileDetailResponse>(
      profileUrl(name),
      { params: { scope } },
    );
    return data;
  }

  static async saveProfile(
    name: string,
    profile: AgentProfileSaveInput,
    scope: AgentProfileScope,
  ): Promise<void> {
    await openHands.post(profileUrl(name), profile, { params: { scope } });
  }

  static async deleteProfile(
    name: string,
    scope: AgentProfileScope,
  ): Promise<void> {
    await openHands.delete(profileUrl(name), { params: { scope } });
  }

  static async renameProfile(
    name: string,
    newName: string,
    scope: AgentProfileScope,
  ): Promise<void> {
    await openHands.post(
      `${profileUrl(name)}/rename`,
      { new_name: newName },
      { params: { scope } },
    );
  }

  /** Pointer-only; the id may belong to either scope. */
  static async activateProfile(id: string): Promise<void> {
    await openHands.post(`${profileUrl(id)}/activate`);
  }
}

export default AgentProfilesService;
