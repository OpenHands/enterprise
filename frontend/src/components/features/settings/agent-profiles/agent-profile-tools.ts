import type { AgentProfileTool } from "#/api/agent-profiles-service/agent-profiles-service.api";

/**
 * Mirrors ``openhands.sdk.tool.defaults`` (SDK 1.58.1). Tool names are a
 * wire contract: ``tools: null`` means the SDK standard set, which already
 * includes ``switch_llm`` but not ``task_tool_set``. "The standard set plus
 * or minus one tool" is not expressible, so toggling either switch pins an
 * explicit list, exactly as the SDK's ``fold_retired_tool_switches`` does.
 * ``browser_tool_set`` is part of the pinned list because cloud launches
 * enable the browser.
 */
export const STANDARD_TOOL_NAMES = [
  "terminal",
  "file_editor",
  "task_tracker",
  "browser_tool_set",
] as const;
export const SUB_AGENT_TOOL_NAME = "task_tool_set";
export const SWITCH_LLM_TOOL_NAME = "switch_llm";

/** Built-in class names the SDK's ``canonical_tool_name`` collapses onto the toggled tools. */
const TOOL_NAME_ALIASES: Record<string, string> = {
  TaskToolSet: SUB_AGENT_TOOL_NAME,
  SwitchLLMTool: SWITCH_LLM_TOOL_NAME,
};

function canonicalToolName(name: string): string {
  return TOOL_NAME_ALIASES[name] ?? name;
}

export interface ToolToggles {
  enableSubAgents: boolean;
  enableSwitchLlmTool: boolean;
}

/** What ``tools: null`` means: no sub-agents, LLM switching on. */
export const DEFAULT_TOOL_TOGGLES: ToolToggles = {
  enableSubAgents: false,
  enableSwitchLlmTool: true,
};

/** Read the two toggles back from a stored profile's ``tools``. */
export function readToolToggles(
  tools: AgentProfileTool[] | null | undefined,
): ToolToggles {
  if (!Array.isArray(tools)) return DEFAULT_TOOL_TOGGLES;
  const names = new Set(tools.map((tool) => canonicalToolName(tool.name)));
  return {
    enableSubAgents: names.has(SUB_AGENT_TOOL_NAME),
    enableSwitchLlmTool: names.has(SWITCH_LLM_TOOL_NAME),
  };
}

function toggleTool(
  entries: AgentProfileTool[],
  name: string,
  enabled: boolean,
): AgentProfileTool[] {
  if (!enabled) {
    return entries.filter((entry) => canonicalToolName(entry.name) !== name);
  }
  if (entries.some((entry) => canonicalToolName(entry.name) === name)) {
    return entries;
  }
  return [...entries, { name }];
}

/**
 * The ``tools`` to save for the given toggles. A profile that already pins
 * a list (for example one edited in Agent Canvas) keeps its other entries;
 * otherwise the toggles either leave ``null`` (defaults) or pin the
 * standard set plus the chosen switches.
 */
export function buildProfileTools(
  toggles: ToolToggles,
  storedTools: AgentProfileTool[] | null | undefined,
): AgentProfileTool[] | null {
  let entries: AgentProfileTool[];
  if (Array.isArray(storedTools)) {
    entries = [...storedTools];
  } else {
    if (
      toggles.enableSubAgents === DEFAULT_TOOL_TOGGLES.enableSubAgents &&
      toggles.enableSwitchLlmTool === DEFAULT_TOOL_TOGGLES.enableSwitchLlmTool
    ) {
      return null;
    }
    entries = STANDARD_TOOL_NAMES.map((name) => ({ name }));
  }
  entries = toggleTool(entries, SUB_AGENT_TOOL_NAME, toggles.enableSubAgents);
  return toggleTool(entries, SWITCH_LLM_TOOL_NAME, toggles.enableSwitchLlmTool);
}
