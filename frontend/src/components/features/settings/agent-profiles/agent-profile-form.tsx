import { useTranslation } from "react-i18next";
import type {
  AgentKind,
  AgentProfileScope,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import type { ACPProviderConfig } from "#/api/option-service/option.types";
import type { LlmProfileSummary } from "#/api/settings-service/profiles-service.api";
import { AcpCredentialsSection } from "#/components/features/settings/acp-credentials-section";
import { SchemaField } from "#/components/features/settings/sdk-settings/schema-field";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import type { AcpCredentialForm } from "#/hooks/use-acp-credential-form";
import { I18nKey } from "#/i18n/declaration";
import type { SettingsFieldSchema, SettingsSchema } from "#/types/settings";
import { Typography } from "#/ui/typography";
import { CUSTOM_PRESET, detectPreset } from "#/utils/acp-agent-form";
import {
  formControlMultilineFieldClassName,
  formControlSwitchDescriptionClassName,
  formControlSwitchFieldClassName,
} from "#/utils/form-control-classes";
import { isSettingsFieldVisible } from "#/utils/sdk-settings-schema";
import { formatCommand } from "#/utils/shell-tokenize";
import { cn } from "#/utils/utils";
import {
  SYSTEM_PROMPT_MAX_LENGTH,
  isSystemPromptMissing,
  isToolSelectionMissing,
  type AgentProfileFormState,
  type ProfileScopeMode,
  type SystemPromptMode,
} from "./agent-profile-form-state";
import { standardToolNames, toolTogglesFromNames } from "./agent-profile-tools";
import {
  PROFILE_BEHAVIOR_SECTIONS,
  profileBehaviorFields,
} from "./profile-behavior-form";
import { ProfileScopeList, type ProfileScopeItem } from "./profile-scope-list";

interface AgentProfileFormProps {
  state: AgentProfileFormState;
  onChange: (patch: Partial<AgentProfileFormState>) => void;
  isDisabled: boolean;
  /** ``feature_flags.enable_acp``: shows the agent-type selector + ACP fields. */
  isAcpEnabled: boolean;
  acpProviders: ACPProviderConfig[];
  llmProfiles: LlmProfileSummary[];
  /** ``tool_concurrency_limit`` from the agent-settings schema, when exposed. */
  toolConcurrencyField?: SettingsFieldSchema;
  /** The agent-settings schema, for the per-profile condenser / verification fields. */
  schema?: SettingsSchema | null;
  /** The server's tool catalog (user-selectable entries), for the tools picker. */
  tools: ProfileScopeItem[];
  /** True while the tool catalog is still being fetched. */
  toolsLoading?: boolean;
  /** True when the tool catalog could not be loaded. */
  toolsLoadFailed?: boolean;
  /** The member's configured MCP servers (user-scoped). */
  mcpServers: ProfileScopeItem[];
  /** The member's saved secrets (user-scoped). */
  secrets: ProfileScopeItem[];
  /** Skills available to the member; organization-marketplace skills carry a badge. */
  skills: ProfileScopeItem[];
  /** True when the skills list could not be loaded. */
  skillsLoadFailed?: boolean;
  /** Credentials for the selected ACP preset; saved as global secrets. */
  credentialForm: AcpCredentialForm;
  /**
   * The collection being edited. An org profile is OpenHands-only, has no
   * MCP servers and always names its secrets, so the agent-kind selector, the
   * MCP section and the secrets mode switch are left out (OHE-3088).
   */
  profileScope?: AgentProfileScope;
}

function toggleName(list: string[], name: string, checked: boolean): string[] {
  const without = list.filter((entry) => entry !== name);
  return checked ? [...without, name] : without;
}

/**
 * The fields of one Agent Profile. Controlled: the owning view holds the
 * state and persists it. Mirrors the Agent Canvas profile editor for the
 * settings OHE supports (agent kind, LLM profile, tools, parallel tool calls,
 * system prompt, MCP server and secret scopes, skills, ACP launch), narrowed
 * to organization-scoped settings for an org profile.
 */
export function AgentProfileForm({
  state,
  onChange,
  isDisabled,
  isAcpEnabled,
  acpProviders,
  llmProfiles,
  toolConcurrencyField,
  schema,
  tools,
  toolsLoading = false,
  toolsLoadFailed = false,
  mcpServers,
  secrets,
  skills,
  skillsLoadFailed = false,
  credentialForm,
  profileScope = "personal",
}: AgentProfileFormProps) {
  const { t } = useTranslation();
  const isAcp = state.agentKind === "acp";
  const isOrg = profileScope === "org";
  const systemPromptMissing = isSystemPromptMissing(state);
  const toolsMissing = isToolSelectionMissing(state);

  // ── ACP derived state ─────────────────────────────────────────────────────
  const selectedPreset = detectPreset(state.acpCommandText, acpProviders);
  const commandPlaceholder =
    formatCommand(acpProviders[0]?.default_command ?? []) ||
    "npx -y <package-name>";
  // ── Scope catalogs ────────────────────────────────────────────────────────
  // A stored ref whose server/secret is gone rides along so it can be cleared.
  // Same for a pinned tool this server's catalog does not list; it is only
  // flagged as unknown once the catalog has actually loaded.
  const knownTools = new Set(tools.map(({ name }) => name));
  const unlistedTools =
    state.toolsMode === "custom"
      ? state.toolsSelected.filter((name) => !knownTools.has(name))
      : [];
  const unknownTools = toolsLoading || toolsLoadFailed ? [] : unlistedTools;
  const toolCatalog: ProfileScopeItem[] = [
    ...tools,
    ...unlistedTools.map((name) => ({ name, description: null })),
  ];
  const knownMcp = new Set(mcpServers.map(({ name }) => name));
  const danglingMcpRefs =
    state.mcpMode === "custom"
      ? state.mcpSelected.filter((name) => !knownMcp.has(name))
      : [];
  const mcpCatalog: ProfileScopeItem[] = [
    ...mcpServers,
    ...danglingMcpRefs.map((name) => ({ name, description: null })),
  ];
  const knownSecrets = new Set(secrets.map(({ name }) => name));
  // An org profile always names its secrets; a personal one only when narrowed.
  const hasSecretList = isOrg || state.secretsMode === "custom";
  const secretCatalog: ProfileScopeItem[] = [
    ...secrets,
    ...(hasSecretList
      ? state.secretsSelected
          .filter((name) => !knownSecrets.has(name))
          .map((name) => ({ name, description: null }))
      : []),
  ];
  let secretsHintKey = I18nKey.SETTINGS$AGENT_PROFILE_SECRETS_ALL_HINT;
  if (isOrg) {
    secretsHintKey = I18nKey.SETTINGS$AGENT_PROFILE_ORG_SECRETS_HINT;
  } else if (state.secretsMode === "custom") {
    secretsHintKey = I18nKey.SETTINGS$AGENT_PROFILE_SECRETS_CHOOSE_HINT;
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Agent type — only when the ACP feature flag is on; org profiles are OpenHands-only */}
      {isAcpEnabled && !isOrg && (
        <section className="grid gap-4 xl:grid-cols-2">
          <SettingsDropdownInput
            testId="agent-profile-type-selector"
            name="agent-profile-type"
            label={t(I18nKey.SETTINGS$AGENT)}
            items={[
              {
                key: "openhands",
                label: t(I18nKey.SETTINGS$AGENT_TYPE_OPENHANDS),
              },
              { key: "acp", label: t(I18nKey.SETTINGS$AGENT_TYPE_ACP) },
            ]}
            selectedKey={state.agentKind}
            isDisabled={isDisabled}
            onSelectionChange={(key) => {
              if (!key) return;
              const agentKind = key as AgentKind;
              const patch: Partial<AgentProfileFormState> = { agentKind };
              if (agentKind === "acp" && !state.acpCommandText) {
                const preferred = acpProviders[0];
                if (preferred) {
                  patch.acpCommandText = formatCommand(
                    preferred.default_command,
                  );
                  patch.acpModel = "";
                }
              }
              onChange(patch);
            }}
          />
        </section>
      )}

      {/* OpenHands: LLM profile (required) */}
      {!isAcp && (
        <section className="grid gap-4 xl:grid-cols-2">
          {llmProfiles.length > 0 ? (
            <SettingsDropdownInput
              testId="agent-profile-llm-selector"
              name="agent-profile-llm"
              label={t(I18nKey.SETTINGS$AGENT_PROFILE_LLM_LABEL)}
              items={llmProfiles.map((profile) => ({
                key: profile.name,
                label: profile.model
                  ? `${profile.name} (${profile.model})`
                  : profile.name,
              }))}
              selectedKey={state.llmProfileRef}
              isDisabled={isDisabled}
              onSelectionChange={(key) => {
                if (key) onChange({ llmProfileRef: String(key) });
              }}
            />
          ) : (
            <Typography.Paragraph
              testId="agent-profile-no-llm"
              className="text-sm text-red-400"
            >
              {t(I18nKey.SETTINGS$AGENT_PROFILE_NO_LLM)}
            </Typography.Paragraph>
          )}
        </section>
      )}

      {/* OpenHands: tools — the standard set shaped by two switches, or a hand-picked list */}
      {!isAcp && (
        <div
          className="flex flex-col gap-2.5"
          data-testid="agent-profile-tools-section"
        >
          <Typography.Text className="text-sm">
            {t(I18nKey.SETTINGS$AGENT_PROFILE_TOOLS)}
          </Typography.Text>
          <SettingsDropdownInput
            testId="agent-profile-tools-mode"
            name="agent-profile-tools-mode"
            items={[
              {
                key: "standard",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_TOOLS_STANDARD),
              },
              {
                key: "custom",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_TOOLS_CHOOSE),
              },
            ]}
            selectedKey={state.toolsMode}
            isDisabled={isDisabled}
            onSelectionChange={(key) => {
              if (!key || key === state.toolsMode) return;
              const toolsMode = key as ProfileScopeMode;
              // Each mode starts from what the other launches with: the
              // picker is seeded with the standard set as the switches shape
              // it, and the switches are read back from the picked list.
              if (toolsMode === "custom") {
                onChange({
                  toolsMode,
                  toolsSelected: standardToolNames({
                    enableSubAgents: state.enableSubAgents,
                    enableSwitchLlmTool: state.enableSwitchLlmTool,
                  }),
                });
              } else {
                onChange({
                  toolsMode,
                  ...toolTogglesFromNames(state.toolsSelected),
                });
              }
            }}
          />
          {state.toolsMode === "standard" ? (
            <section className="flex flex-col gap-4">
              <div className={formControlSwitchFieldClassName}>
                <SettingsSwitch
                  testId="agent-profile-enable-sub-agents"
                  isToggled={state.enableSubAgents}
                  isDisabled={isDisabled}
                  onToggle={(enableSubAgents) => onChange({ enableSubAgents })}
                >
                  {t(I18nKey.SCHEMA$ENABLE_SUB_AGENTS$LABEL)}
                </SettingsSwitch>
                <Typography.Paragraph
                  className={cn(
                    formControlSwitchDescriptionClassName,
                    "text-tertiary-alt text-xs leading-5",
                  )}
                >
                  {t(I18nKey.SCHEMA$ENABLE_SUB_AGENTS$DESCRIPTION)}
                </Typography.Paragraph>
              </div>
              <div className={formControlSwitchFieldClassName}>
                <SettingsSwitch
                  testId="agent-profile-enable-switch-llm-tool"
                  isToggled={state.enableSwitchLlmTool}
                  isDisabled={isDisabled}
                  onToggle={(enableSwitchLlmTool) =>
                    onChange({ enableSwitchLlmTool })
                  }
                >
                  {t(I18nKey.SCHEMA$ENABLE_SWITCH_LLM_TOOL$LABEL)}
                </SettingsSwitch>
                <Typography.Paragraph
                  className={cn(
                    formControlSwitchDescriptionClassName,
                    "text-tertiary-alt text-xs leading-5",
                  )}
                >
                  {t(I18nKey.SCHEMA$ENABLE_SWITCH_LLM_TOOL$DESCRIPTION)}
                </Typography.Paragraph>
              </div>
            </section>
          ) : (
            <>
              {toolCatalog.length > 0 ? (
                <div className="max-h-80 overflow-y-auto pr-1">
                  <ProfileScopeList
                    testId="agent-profile-tool"
                    items={toolCatalog}
                    selected={state.toolsSelected}
                    isDisabled={isDisabled}
                    onToggle={(name, checked) =>
                      onChange({
                        toolsSelected: toggleName(
                          state.toolsSelected,
                          name,
                          checked,
                        ),
                      })
                    }
                  />
                </div>
              ) : null}
              {toolsLoadFailed ? (
                <Typography.Text
                  testId="agent-profile-tools-load-failed"
                  className="text-xs text-red-400"
                >
                  {t(I18nKey.SETTINGS$AGENT_PROFILE_TOOLS_LOAD_FAILED)}
                </Typography.Text>
              ) : null}
              {toolsMissing ? (
                <Typography.Text
                  testId="agent-profile-tools-required"
                  className="text-xs text-red-400"
                >
                  {t(I18nKey.SETTINGS$AGENT_PROFILE_TOOLS_REQUIRED)}
                </Typography.Text>
              ) : null}
              {unknownTools.length > 0 ? (
                <Typography.Text
                  testId="agent-profile-tools-unknown"
                  className="text-xs text-red-400"
                >
                  {t(I18nKey.SETTINGS$AGENT_PROFILE_TOOLS_UNKNOWN, {
                    names: unknownTools.join(", "),
                  })}
                </Typography.Text>
              ) : null}
            </>
          )}
          <Typography.Text className="text-xs text-tertiary-alt">
            {t(
              state.toolsMode === "custom"
                ? I18nKey.SETTINGS$AGENT_PROFILE_TOOLS_CHOOSE_HINT
                : I18nKey.SETTINGS$AGENT_PROFILE_TOOLS_STANDARD_HINT,
            )}
          </Typography.Text>
        </div>
      )}

      {/* OpenHands: system prompt — default, default + instructions, or persona */}
      {!isAcp && (
        <div
          className="flex flex-col gap-2.5"
          data-testid="agent-profile-system-prompt-section"
        >
          <SettingsDropdownInput
            testId="agent-profile-system-prompt-mode"
            name="agent-profile-system-prompt-mode"
            label={t(I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT)}
            items={[
              {
                key: "standard",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_STANDARD),
              },
              {
                key: "append",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_APPEND),
              },
              {
                key: "custom",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_CUSTOM),
              },
            ]}
            selectedKey={state.systemPromptMode}
            isDisabled={isDisabled}
            onSelectionChange={(key) => {
              if (key) onChange({ systemPromptMode: key as SystemPromptMode });
            }}
          />
          {state.systemPromptMode !== "standard" ? (
            <textarea
              data-testid="agent-profile-system-prompt"
              aria-label={t(I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT)}
              className={cn(
                formControlMultilineFieldClassName,
                "min-h-48 font-mono placeholder:italic",
              )}
              value={state.systemPromptText}
              maxLength={SYSTEM_PROMPT_MAX_LENGTH}
              placeholder={t(
                state.systemPromptMode === "custom"
                  ? I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_PLACEHOLDER
                  : I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_APPEND_PLACEHOLDER,
              )}
              disabled={isDisabled}
              onChange={(e) => onChange({ systemPromptText: e.target.value })}
            />
          ) : null}
          {systemPromptMissing ? (
            <Typography.Text
              testId="agent-profile-system-prompt-required"
              className="text-xs text-red-400"
            >
              {t(I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_REQUIRED)}
            </Typography.Text>
          ) : null}
          <Typography.Text className="text-xs text-tertiary-alt">
            {t(
              {
                standard:
                  I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_STANDARD_HINT,
                append:
                  I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_APPEND_HINT,
                custom:
                  I18nKey.SETTINGS$AGENT_PROFILE_SYSTEM_PROMPT_CUSTOM_HINT,
              }[state.systemPromptMode],
            )}
          </Typography.Text>
        </div>
      )}

      {/* OpenHands: parallel tool calls — only when the schema exposes it */}
      {!isAcp && toolConcurrencyField ? (
        <section className="grid gap-4 xl:grid-cols-2">
          <SchemaField
            field={toolConcurrencyField}
            value={state.toolConcurrency}
            isDisabled={isDisabled}
            onChange={(toolConcurrency) => onChange({ toolConcurrency })}
          />
        </section>
      ) : null}

      {/* ACP: preset, command, model, credentials */}
      {isAcp && (
        <>
          <SettingsDropdownInput
            testId="agent-profile-preset-selector"
            name="agent-profile-preset"
            label={t(I18nKey.SETTINGS$AGENT_PRESET)}
            items={[
              ...acpProviders.map((provider) => ({
                key: provider.key,
                label: provider.display_name,
              })),
              {
                key: CUSTOM_PRESET,
                label: t(I18nKey.SETTINGS$AGENT_PRESET_CUSTOM),
              },
            ]}
            selectedKey={selectedPreset}
            isDisabled={isDisabled}
            onSelectionChange={(key) => {
              if (!key) return;
              const preset = String(key);
              const provider = acpProviders.find(({ key: k }) => k === preset);
              if (provider) {
                onChange({
                  acpCommandText: formatCommand(provider.default_command),
                  acpModel: "",
                });
              } else if (preset === CUSTOM_PRESET) {
                onChange({ acpCommandText: "", acpModel: "" });
              }
            }}
          />

          <div className="flex flex-col gap-2.5">
            <Typography.Text className="text-sm">
              {t(I18nKey.SETTINGS$MCP_COMMAND)}
            </Typography.Text>
            <textarea
              data-testid="agent-profile-command-input"
              className={cn(
                formControlMultilineFieldClassName,
                "font-mono min-h-[60px] resize-y",
              )}
              value={state.acpCommandText}
              placeholder={commandPlaceholder}
              disabled={isDisabled}
              onChange={(e) => {
                const next = e.target.value;
                const patch: Partial<AgentProfileFormState> = {
                  acpCommandText: next,
                };
                // Model ids are provider-specific: fall back to the agent
                // default when the typed command switches provider.
                const nextPreset = detectPreset(next, acpProviders);
                if (nextPreset !== selectedPreset) {
                  patch.acpModel = "";
                }
                onChange(patch);
              }}
            />
            <Typography.Text className="text-xs text-[var(--oh-muted)]">
              {t(I18nKey.SETTINGS$AGENT_COMMAND_HINT)}
            </Typography.Text>
          </div>

          {/* Blank means the agent's own default; a running conversation
              lists the models its session offers in the chat header. */}
          <div className="flex flex-col gap-1.5">
            <SettingsInput
              testId="agent-profile-model-input"
              label={t(I18nKey.SETTINGS$AGENT_MODEL)}
              type="text"
              className="w-full"
              value={state.acpModel}
              placeholder={t(I18nKey.SETTINGS$AGENT_MODEL_AGENT_DEFAULT)}
              showOptionalTag
              isDisabled={isDisabled}
              onChange={(acpModel) => onChange({ acpModel })}
            />
            <Typography.Text className="text-xs text-[var(--oh-muted)]">
              {t(I18nKey.SETTINGS$AGENT_MODEL_HINT)}
            </Typography.Text>
          </div>

          {credentialForm.fields.length > 0 && (
            <>
              <hr className="border-[var(--oh-border)]" />
              <AcpCredentialsSection form={credentialForm} />
            </>
          )}
        </>
      )}

      {/* MCP servers (both kinds): all configured, or an explicit subset.
          Not for org profiles: MCP servers are per member, so an org profile
          launches with none. */}
      {!isOrg && (
        <div className="flex flex-col gap-2.5">
          <Typography.Text className="text-sm">
            {t(I18nKey.SETTINGS$AGENT_PROFILE_MCP)}
          </Typography.Text>
          <SettingsDropdownInput
            testId="agent-profile-mcp-mode"
            name="agent-profile-mcp-mode"
            items={[
              {
                key: "standard",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_MCP_ALL),
              },
              {
                key: "custom",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_MCP_CHOOSE),
              },
            ]}
            selectedKey={state.mcpMode}
            isDisabled={isDisabled}
            onSelectionChange={(key) => {
              if (!key) return;
              const mcpMode = key as ProfileScopeMode;
              const patch: Partial<AgentProfileFormState> = { mcpMode };
              // Seed a first switch to "choose" with every configured server,
              // so narrowing starts from what the agent had.
              if (mcpMode === "custom" && state.mcpSelected.length === 0) {
                patch.mcpSelected = mcpServers.map(({ name }) => name);
              }
              onChange(patch);
            }}
          />
          {mcpCatalog.length > 0 ? (
            <ProfileScopeList
              testId="agent-profile-mcp"
              items={mcpCatalog}
              selected={
                state.mcpMode === "custom"
                  ? state.mcpSelected
                  : mcpCatalog.map(({ name }) => name)
              }
              isDisabled={isDisabled || state.mcpMode === "standard"}
              onToggle={(name, checked) =>
                onChange({
                  mcpSelected: toggleName(state.mcpSelected, name, checked),
                })
              }
            />
          ) : (
            <Typography.Text className="text-xs text-tertiary-alt">
              {t(I18nKey.SETTINGS$AGENT_PROFILE_MCP_NONE)}
            </Typography.Text>
          )}
          {danglingMcpRefs.length > 0 ? (
            <Typography.Text
              testId="agent-profile-mcp-dangling"
              className="text-xs text-red-400"
            >
              {t(I18nKey.SETTINGS$AGENT_PROFILE_MCP_DANGLING, {
                names: danglingMcpRefs.join(", "),
              })}
            </Typography.Text>
          ) : null}
          <Typography.Text className="text-xs text-tertiary-alt">
            {t(
              state.mcpMode === "custom"
                ? I18nKey.SETTINGS$AGENT_PROFILE_MCP_CHOOSE_HINT
                : I18nKey.SETTINGS$AGENT_PROFILE_MCP_ALL_HINT,
            )}
          </Typography.Text>
        </div>
      )}

      {/* OpenHands: condenser and verification, from the agent-settings schema */}
      {!isAcp &&
        PROFILE_BEHAVIOR_SECTIONS.map((section) => {
          const fields = profileBehaviorFields(schema, section);
          if (fields.length === 0) return null;
          const values = state[section];
          return (
            <div
              key={section}
              className="flex flex-col gap-2.5"
              data-testid={`agent-profile-${section}`}
            >
              <Typography.Text className="text-sm">
                {t(
                  section === "condenser"
                    ? I18nKey.SETTINGS$NAV_CONDENSER
                    : I18nKey.SETTINGS$NAV_VERIFICATION,
                )}
              </Typography.Text>
              <div className="grid gap-4 xl:grid-cols-2">
                {fields
                  .filter((field) => isSettingsFieldVisible(field, values))
                  .map((field) => (
                    <SchemaField
                      key={field.key}
                      field={field}
                      value={values[field.key] ?? ""}
                      isDisabled={isDisabled}
                      onChange={(value) => {
                        const next = { ...values, [field.key]: value };
                        onChange(
                          section === "condenser"
                            ? { condenser: next }
                            : { verification: next },
                        );
                      }}
                    />
                  ))}
              </div>
            </div>
          );
        })}

      {/* Secrets (both kinds): no restriction, or an explicit subset.
          An org profile always lists the org-shared secrets it may use. */}
      <div className="flex flex-col gap-2.5">
        <Typography.Text className="text-sm">
          {t(I18nKey.SETTINGS$AGENT_PROFILE_SECRETS)}
        </Typography.Text>
        {!isOrg ? (
          <SettingsDropdownInput
            testId="agent-profile-secrets-mode"
            name="agent-profile-secrets-mode"
            items={[
              {
                key: "standard",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_SECRETS_ALL),
              },
              {
                key: "custom",
                label: t(I18nKey.SETTINGS$AGENT_PROFILE_SECRETS_CHOOSE),
              },
            ]}
            selectedKey={state.secretsMode}
            isDisabled={isDisabled}
            onSelectionChange={(key) => {
              if (!key) return;
              const secretsMode = key as ProfileScopeMode;
              const patch: Partial<AgentProfileFormState> = { secretsMode };
              // An external agent needs its provider credentials to sign in:
              // pre-select them when narrowing the scope for an ACP profile.
              if (secretsMode === "custom" && isAcp) {
                const providerSecrets = credentialForm.fields.map(
                  ({ name }) => name,
                );
                patch.secretsSelected = [
                  ...new Set([...state.secretsSelected, ...providerSecrets]),
                ];
              }
              onChange(patch);
            }}
          />
        ) : null}
        {secretCatalog.length > 0 ? (
          <ProfileScopeList
            testId="agent-profile-secret"
            items={secretCatalog}
            selected={
              hasSecretList
                ? state.secretsSelected
                : secretCatalog.map(({ name }) => name)
            }
            isDisabled={isDisabled || !hasSecretList}
            onToggle={(name, checked) =>
              onChange({
                secretsSelected: toggleName(
                  state.secretsSelected,
                  name,
                  checked,
                ),
              })
            }
          />
        ) : (
          <Typography.Text className="text-xs text-tertiary-alt">
            {t(
              isOrg
                ? I18nKey.SETTINGS$AGENT_PROFILE_ORG_SECRETS_NONE
                : I18nKey.SETTINGS$AGENT_PROFILE_SECRETS_NONE,
            )}
          </Typography.Text>
        )}
        <Typography.Text className="text-xs text-tertiary-alt">
          {t(secretsHintKey)}
        </Typography.Text>
      </div>

      {/* OpenHands: skills — all on by default, unchecked ones are denied */}
      {!isAcp && (
        <div
          className="flex flex-col gap-2.5"
          data-testid="agent-profile-skills-section"
        >
          <Typography.Text className="text-sm">
            {t(I18nKey.SETTINGS$AGENT_PROFILE_SKILLS)}
          </Typography.Text>
          {skills.length > 0 ? (
            <div className="max-h-80 overflow-y-auto pr-1">
              <ProfileScopeList
                testId="agent-profile-skill"
                items={skills}
                selected={skills
                  .map(({ name }) => name)
                  .filter((name) => !state.disabledSkills.includes(name))}
                isDisabled={isDisabled}
                onToggle={(name, checked) =>
                  onChange({
                    disabledSkills: toggleName(
                      state.disabledSkills,
                      name,
                      !checked,
                    ),
                  })
                }
              />
            </div>
          ) : (
            <Typography.Text
              testId="agent-profile-skills-empty"
              className={cn(
                "text-xs",
                skillsLoadFailed ? "text-red-400" : "text-tertiary-alt",
              )}
            >
              {t(
                skillsLoadFailed
                  ? I18nKey.SETTINGS$AGENT_PROFILE_SKILLS_LOAD_FAILED
                  : I18nKey.SETTINGS$AGENT_PROFILE_SKILLS_NONE,
              )}
            </Typography.Text>
          )}
          <Typography.Text className="text-xs text-tertiary-alt">
            {t(
              isOrg
                ? I18nKey.SETTINGS$AGENT_PROFILE_ORG_SKILLS_HINT
                : I18nKey.SETTINGS$AGENT_PROFILE_SKILLS_HINT,
            )}
          </Typography.Text>
        </div>
      )}
    </div>
  );
}
