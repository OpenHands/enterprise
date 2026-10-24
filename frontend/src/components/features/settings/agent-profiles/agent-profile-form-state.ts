import type {
  AgentKind,
  AgentProfile,
  AgentProfileSaveInput,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import type { ACPProviderConfig } from "#/api/option-service/option.types";
import type { SettingsFieldSchema } from "#/types/settings";
import { CUSTOM_PRESET, detectPreset } from "#/utils/acp-agent-form";
import {
  coerceFieldValue,
  normalizeFieldValue,
} from "#/utils/sdk-settings-schema";
import { formatCommand, tokenizeCommand } from "#/utils/shell-tokenize";
import {
  DEFAULT_TOOL_TOGGLES,
  buildProfileTools,
  readToolToggles,
} from "./agent-profile-tools";

/** Backend rule (SDK ``PROFILE_NAME_PATTERN``): leading alphanumeric, 1–64 chars. */
export const AGENT_PROFILE_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/** Picker state for a profile's scope fields: server default, or an explicit list. */
export type ProfileScopeMode = "standard" | "custom";

/**
 * Read a stored profile's named scope (MCP servers or secrets) into picker
 * state. Tri-state on the wire: `null`/absent = no restriction, an array =
 * only those names (`[]` = none).
 */
export function readProfileScope(value: unknown): {
  mode: ProfileScopeMode;
  selected: string[];
} {
  if (!Array.isArray(value)) return { mode: "standard", selected: [] };
  return {
    mode: "custom",
    selected: value.filter((name): name is string => typeof name === "string"),
  };
}

export interface AgentProfileFormState {
  agentKind: AgentKind;
  llmProfileRef: string;
  enableSubAgents: boolean;
  enableSwitchLlmTool: boolean;
  /** ``SchemaField`` value for ``tool_concurrency_limit`` ("" when unknown). */
  toolConcurrency: string | boolean;
  mcpMode: ProfileScopeMode;
  mcpSelected: string[];
  secretsMode: ProfileScopeMode;
  secretsSelected: string[];
  /** Shell string shown in the ACP command textarea. */
  acpCommandText: string;
  acpModel: string;
}

export interface AgentProfileFormDeps {
  acpProviders: ACPProviderConfig[];
  toolConcurrencyField?: SettingsFieldSchema;
  defaultLlmProfileRef: string;
}

export function newAgentProfileFormState(
  deps: AgentProfileFormDeps,
): AgentProfileFormState {
  return {
    agentKind: "openhands",
    llmProfileRef: deps.defaultLlmProfileRef,
    enableSubAgents: DEFAULT_TOOL_TOGGLES.enableSubAgents,
    enableSwitchLlmTool: DEFAULT_TOOL_TOGGLES.enableSwitchLlmTool,
    toolConcurrency: deps.toolConcurrencyField
      ? normalizeFieldValue(deps.toolConcurrencyField, undefined)
      : "",
    mcpMode: "standard",
    mcpSelected: [],
    secretsMode: "standard",
    secretsSelected: [],
    acpCommandText: "",
    acpModel: "",
  };
}

/** Seed the editor from a stored profile (the inverse of the builder below). */
export function agentProfileFormStateFromProfile(
  profile: AgentProfile,
  deps: AgentProfileFormDeps,
): AgentProfileFormState {
  const base = newAgentProfileFormState(deps);
  const mcp = readProfileScope(profile.mcp_server_refs);
  const secrets = readProfileScope(profile.secret_refs);
  const scoped = {
    mcpMode: mcp.mode,
    mcpSelected: mcp.selected,
    secretsMode: secrets.mode,
    secretsSelected: secrets.selected,
  };

  if (profile.agent_kind === "acp") {
    const provider = deps.acpProviders.find(
      ({ key }) => key === profile.acp_server,
    );
    // The profile splits the launch into ``acp_command`` (shell string) and
    // ``acp_args``; the textarea shows them as one command line. A preset's
    // default command stays implicit (so the preset is recognised); its
    // extra ``acp_args`` are kept by the save instead.
    const tokens = profile.acp_command
      ? [...tokenizeCommand(profile.acp_command), ...(profile.acp_args ?? [])]
      : (provider?.default_command ?? []);
    return {
      ...base,
      ...scoped,
      agentKind: "acp",
      acpCommandText: formatCommand(tokens),
      // ``null`` means "the provider's current default"; don't pin it.
      acpModel: profile.acp_model ?? "",
    };
  }

  const toggles = readToolToggles(profile.tools);
  return {
    ...base,
    ...scoped,
    agentKind: "openhands",
    llmProfileRef: profile.llm_profile_ref,
    enableSubAgents: toggles.enableSubAgents,
    enableSwitchLlmTool: toggles.enableSwitchLlmTool,
    toolConcurrency: deps.toolConcurrencyField
      ? normalizeFieldValue(
          deps.toolConcurrencyField,
          profile.tool_concurrency_limit,
        )
      : "",
  };
}

export function isAgentProfileFormValid(state: AgentProfileFormState): boolean {
  if (state.agentKind === "acp") {
    return tokenizeCommand(state.acpCommandText).length > 0;
  }
  return state.llmProfileRef.trim().length > 0;
}

/**
 * Turn editor state into the variant-specific save body. Throws a
 * user-facing ``Error`` when the parallel-tool-calls value fails the
 * schema's coercion/min/max rules (``coerceFieldValue``). ``stored`` is the
 * profile being edited, so the two tool toggles only add or remove their own
 * entries and a preset's extra ``acp_args`` survive the save.
 */
export function buildAgentProfileSaveInput(
  state: AgentProfileFormState,
  deps: AgentProfileFormDeps,
  stored?: AgentProfile | null,
): AgentProfileSaveInput {
  const mcpServerRefs = state.mcpMode === "custom" ? state.mcpSelected : null;
  const secretRefs =
    state.secretsMode === "custom" ? state.secretsSelected : null;

  if (state.agentKind === "acp") {
    const tokens = tokenizeCommand(state.acpCommandText);
    const preset = detectPreset(state.acpCommandText, deps.acpProviders);
    return {
      agent_kind: "acp",
      acp_server: preset,
      // A preset's default command is implied by ``acp_server``; only a
      // hand-written command is stored, as one shell string.
      acp_command: preset === CUSTOM_PRESET ? formatCommand(tokens) : null,
      // A preset keeps the extra args the stored profile launched with; a
      // hand-written command carries everything itself.
      acp_args:
        preset !== CUSTOM_PRESET &&
        stored?.agent_kind === "acp" &&
        stored.acp_server === preset
          ? (stored.acp_args ?? null)
          : null,
      acp_model: state.acpModel.trim() || null,
      mcp_server_refs: mcpServerRefs,
      secret_refs: secretRefs,
    };
  }

  const input: AgentProfileSaveInput = {
    agent_kind: "openhands",
    llm_profile_ref: state.llmProfileRef,
    tools: buildProfileTools(
      {
        enableSubAgents: state.enableSubAgents,
        enableSwitchLlmTool: state.enableSwitchLlmTool,
      },
      stored?.agent_kind === "openhands" ? stored.tools : undefined,
    ),
    mcp_server_refs: mcpServerRefs,
    secret_refs: secretRefs,
  };
  if (deps.toolConcurrencyField) {
    const coerced = coerceFieldValue(
      deps.toolConcurrencyField,
      state.toolConcurrency,
    );
    // Non-nullable int (default 1); skip an empty input rather than send the
    // ``null`` the backend would reject.
    if (typeof coerced === "number") {
      input.tool_concurrency_limit = coerced;
    }
  }
  return input;
}
