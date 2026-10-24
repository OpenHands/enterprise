import type {
  AgentKind,
  AgentProfile,
  AgentProfileSaveInput,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import type { ACPProviderConfig } from "#/api/option-service/option.types";
import type { SettingsFieldSchema, SettingsSchema } from "#/types/settings";
import { CUSTOM_PRESET, detectPreset } from "#/utils/acp-agent-form";
import {
  coerceFieldValue,
  normalizeFieldValue,
  type SettingsFormValues,
} from "#/utils/sdk-settings-schema";
import { formatCommand, tokenizeCommand } from "#/utils/shell-tokenize";
import {
  DEFAULT_TOOL_TOGGLES,
  buildPinnedTools,
  buildProfileTools,
  isStandardToolList,
  readToolNames,
  readToolToggles,
} from "./agent-profile-tools";
import {
  fromBehaviorFormValues,
  toBehaviorFormValues,
} from "./profile-behavior-form";

/**
 * How the profile shapes the system prompt: the OpenHands default, the
 * default plus appended instructions (``system_message_suffix``), or a
 * custom persona replacing the built-in one (``persona``).
 */
export type SystemPromptMode = "standard" | "append" | "custom";
/** SDK ``OpenHandsAgentProfile.persona`` / ``system_message_suffix`` limit. */
export const SYSTEM_PROMPT_MAX_LENGTH = 65536;

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
  /** The standard tool set (shaped by the two toggles above), or a hand-picked list. */
  toolsMode: ProfileScopeMode;
  /** Canonical tool names of the hand-picked list (``toolsMode === "custom"``). */
  toolsSelected: string[];
  /** ``SchemaField`` value for ``tool_concurrency_limit`` ("" when unknown). */
  toolConcurrency: string | boolean;
  systemPromptMode: SystemPromptMode;
  systemPromptText: string;
  /** ``SchemaField`` values of the profile's condenser section, by field key. */
  condenser: SettingsFormValues;
  /** ``SchemaField`` values of the profile's verification section, by field key. */
  verification: SettingsFormValues;
  mcpMode: ProfileScopeMode;
  mcpSelected: string[];
  secretsMode: ProfileScopeMode;
  secretsSelected: string[];
  /** Skill names this profile keeps the agent from loading (``disabled_skills``). */
  disabledSkills: string[];
  /** Shell string shown in the ACP command textarea. */
  acpCommandText: string;
  acpModel: string;
}

export interface AgentProfileFormDeps {
  acpProviders: ACPProviderConfig[];
  toolConcurrencyField?: SettingsFieldSchema;
  /** The agent-settings schema; its condenser / verification sections are edited per profile. */
  schema?: SettingsSchema | null;
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
    toolsMode: "standard",
    toolsSelected: [],
    toolConcurrency: deps.toolConcurrencyField
      ? normalizeFieldValue(deps.toolConcurrencyField, undefined)
      : "",
    systemPromptMode: "standard",
    systemPromptText: "",
    condenser: toBehaviorFormValues(deps.schema, "condenser", undefined),
    verification: toBehaviorFormValues(deps.schema, "verification", undefined),
    mcpMode: "standard",
    mcpSelected: [],
    secretsMode: "standard",
    secretsSelected: [],
    disabledSkills: [],
    acpCommandText: "",
    acpModel: "",
  };
}

/** A persona replaces the default prompt; a suffix extends it. */
function systemPromptModeFor(
  persona: string,
  suffix: string,
): SystemPromptMode {
  if (persona) return "custom";
  if (suffix) return "append";
  return "standard";
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
  // A list the toggles could not have produced opens in the tools picker.
  const toolsMode: ProfileScopeMode = isStandardToolList(profile.tools)
    ? "standard"
    : "custom";
  const persona = profile.persona ?? "";
  const suffix = profile.system_message_suffix ?? "";
  return {
    ...base,
    ...scoped,
    agentKind: "openhands",
    llmProfileRef: profile.llm_profile_ref,
    enableSubAgents: toggles.enableSubAgents,
    enableSwitchLlmTool: toggles.enableSwitchLlmTool,
    toolsMode,
    toolsSelected: toolsMode === "custom" ? readToolNames(profile.tools) : [],
    disabledSkills: profile.disabled_skills ?? [],
    toolConcurrency: deps.toolConcurrencyField
      ? normalizeFieldValue(
          deps.toolConcurrencyField,
          profile.tool_concurrency_limit,
        )
      : "",
    systemPromptMode: systemPromptModeFor(persona, suffix),
    systemPromptText: persona || suffix,
    condenser: toBehaviorFormValues(
      deps.schema,
      "condenser",
      profile.condenser,
    ),
    verification: toBehaviorFormValues(
      deps.schema,
      "verification",
      profile.verification,
    ),
  };
}

/** A non-default system prompt mode needs text to save. */
export function isSystemPromptMissing(state: AgentProfileFormState): boolean {
  return (
    state.agentKind === "openhands" &&
    state.systemPromptMode !== "standard" &&
    state.systemPromptText.trim().length === 0
  );
}

/** A hand-picked tool list needs at least one tool to save. */
export function isToolSelectionMissing(state: AgentProfileFormState): boolean {
  return (
    state.agentKind === "openhands" &&
    state.toolsMode === "custom" &&
    state.toolsSelected.length === 0
  );
}

export function isAgentProfileFormValid(state: AgentProfileFormState): boolean {
  if (state.agentKind === "acp") {
    return tokenizeCommand(state.acpCommandText).length > 0;
  }
  return (
    state.llmProfileRef.trim().length > 0 &&
    !isSystemPromptMissing(state) &&
    !isToolSelectionMissing(state)
  );
}

/**
 * Turn editor state into the variant-specific save body. Throws a
 * user-facing ``Error`` when the parallel-tool-calls value fails the
 * schema's coercion/min/max rules (``coerceFieldValue``). ``stored`` is the
 * profile being edited, so a pinned tool entry keeps its ``params``, the two
 * tool toggles only add or remove their own entries and a preset's extra
 * ``acp_args`` survive the save.
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

  const storedTools =
    stored?.agent_kind === "openhands" ? stored.tools : undefined;
  const input: AgentProfileSaveInput = {
    agent_kind: "openhands",
    llm_profile_ref: state.llmProfileRef,
    // "Standard set" rebuilds from the toggles unless the stored list already
    // is the standard set (then its entries, params included, are kept).
    tools:
      state.toolsMode === "custom"
        ? buildPinnedTools(state.toolsSelected, storedTools)
        : buildProfileTools(
            {
              enableSubAgents: state.enableSubAgents,
              enableSwitchLlmTool: state.enableSwitchLlmTool,
            },
            isStandardToolList(storedTools) ? storedTools : undefined,
          ),
    mcp_server_refs: mcpServerRefs,
    secret_refs: secretRefs,
    // One of the two carries the text; the other is cleared so a mode
    // switch never leaves both set.
    system_message_suffix:
      state.systemPromptMode === "append" && state.systemPromptText.trim()
        ? state.systemPromptText
        : null,
    persona:
      state.systemPromptMode === "custom" && state.systemPromptText.trim()
        ? state.systemPromptText
        : null,
    disabled_skills: state.disabledSkills,
  };
  // Only sections the schema exposes are rewritten; otherwise the stored
  // objects ride along through the whole-profile merge untouched.
  if (Object.keys(state.condenser).length > 0) {
    input.condenser = fromBehaviorFormValues(
      deps.schema,
      "condenser",
      state.condenser,
    );
  }
  if (Object.keys(state.verification).length > 0) {
    input.verification = fromBehaviorFormValues(
      deps.schema,
      "verification",
      state.verification,
    );
  }
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
