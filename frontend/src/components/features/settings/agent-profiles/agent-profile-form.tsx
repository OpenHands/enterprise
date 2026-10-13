import { useTranslation } from "react-i18next";
import type { AgentKind } from "#/api/agent-profiles-service/agent-profiles-service.api";
import type { ACPProviderConfig } from "#/api/option-service/option.types";
import type { LlmProfileSummary } from "#/api/settings-service/profiles-service.api";
import { AcpCredentialsSection } from "#/components/features/settings/acp-credentials-section";
import { SchemaField } from "#/components/features/settings/sdk-settings/schema-field";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import type { AcpCredentialForm } from "#/hooks/use-acp-credential-form";
import { I18nKey } from "#/i18n/declaration";
import type { SettingsFieldSchema } from "#/types/settings";
import { Typography } from "#/ui/typography";
import {
  CUSTOM_MODEL_KEY,
  CUSTOM_PRESET,
  detectPreset,
  isKnownModel,
} from "#/utils/acp-agent-form";
import {
  formControlMultilineFieldClassName,
  formControlSwitchDescriptionClassName,
  formControlSwitchFieldClassName,
} from "#/utils/form-control-classes";
import { formatCommand } from "#/utils/shell-tokenize";
import { cn } from "#/utils/utils";
import type {
  AgentProfileFormState,
  ProfileScopeMode,
} from "./agent-profile-form-state";
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
  /** The member's configured MCP servers (user-scoped). */
  mcpServers: ProfileScopeItem[];
  /** The member's saved secrets (user-scoped). */
  secrets: ProfileScopeItem[];
  /** Credentials for the selected ACP preset; saved as global secrets. */
  credentialForm: AcpCredentialForm;
}

function toggleName(list: string[], name: string, checked: boolean): string[] {
  const without = list.filter((entry) => entry !== name);
  return checked ? [...without, name] : without;
}

/**
 * The fields of one Agent Profile. Controlled: the owning view holds the
 * state and persists it. Mirrors the Agent Canvas profile editor for the
 * settings OHE supports (agent kind, LLM profile, sub-agents, LLM switching,
 * parallel tool calls, MCP server and secret scopes, ACP launch).
 */
export function AgentProfileForm({
  state,
  onChange,
  isDisabled,
  isAcpEnabled,
  acpProviders,
  llmProfiles,
  toolConcurrencyField,
  mcpServers,
  secrets,
  credentialForm,
}: AgentProfileFormProps) {
  const { t } = useTranslation();
  const isAcp = state.agentKind === "acp";

  // ── ACP derived state ─────────────────────────────────────────────────────
  const selectedPreset = detectPreset(state.acpCommandText, acpProviders);
  const selectedProvider = acpProviders.find(
    ({ key }) => key === selectedPreset,
  );
  const commandPlaceholder =
    formatCommand(acpProviders[0]?.default_command ?? []) ||
    "npx -y <package-name>";
  const modelSuggestions = selectedProvider?.available_models ?? [];
  const hasModelSuggestions = modelSuggestions.length > 0;
  const selectedModelKey = isKnownModel(selectedProvider, state.acpModel)
    ? state.acpModel
    : CUSTOM_MODEL_KEY;

  // ── Scope catalogs ────────────────────────────────────────────────────────
  // A stored ref whose server/secret is gone rides along so it can be cleared.
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
  const secretCatalog: ProfileScopeItem[] = [
    ...secrets,
    ...(state.secretsMode === "custom"
      ? state.secretsSelected
          .filter((name) => !knownSecrets.has(name))
          .map((name) => ({ name, description: null }))
      : []),
  ];

  return (
    <div className="flex flex-col gap-6">
      {/* Agent type — only when the ACP feature flag is on */}
      {isAcpEnabled && (
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
                  patch.acpModel = preferred.default_model || "";
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

      {/* OpenHands: sub-agents + LLM switching */}
      {!isAcp && (
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
                  acpModel: provider.default_model || "",
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
                // Sync the model when the typed command switches provider.
                const nextPreset = detectPreset(next, acpProviders);
                if (nextPreset !== selectedPreset) {
                  const nextProvider = acpProviders.find(
                    ({ key }) => key === nextPreset,
                  );
                  patch.acpModel = nextProvider?.default_model || "";
                }
                onChange(patch);
              }}
            />
            <Typography.Text className="text-xs text-[var(--oh-muted)]">
              {t(I18nKey.SETTINGS$AGENT_COMMAND_HINT)}
            </Typography.Text>
          </div>

          <div className="flex flex-col gap-1.5">
            {hasModelSuggestions && (
              <SettingsDropdownInput
                testId="agent-profile-model-selector"
                name="agent-profile-model"
                label={t(I18nKey.SETTINGS$AGENT_MODEL)}
                items={[
                  ...modelSuggestions.map((model) => ({
                    key: model.id,
                    label: model.label,
                  })),
                  {
                    key: CUSTOM_MODEL_KEY,
                    label: t(I18nKey.SETTINGS$AGENT_PRESET_CUSTOM),
                  },
                ]}
                selectedKey={selectedModelKey}
                isDisabled={isDisabled}
                onSelectionChange={(key) => {
                  if (!key) return;
                  const modelKey = String(key);
                  onChange({
                    acpModel: modelKey === CUSTOM_MODEL_KEY ? "" : modelKey,
                  });
                }}
              />
            )}
            {selectedModelKey === CUSTOM_MODEL_KEY && (
              <SettingsInput
                testId="agent-profile-model-input"
                label={
                  hasModelSuggestions
                    ? t(I18nKey.SETTINGS$AGENT_CUSTOM_MODEL)
                    : t(I18nKey.SETTINGS$AGENT_MODEL)
                }
                type="text"
                className="w-full"
                value={state.acpModel}
                showOptionalTag
                isDisabled={isDisabled}
                onChange={(acpModel) => onChange({ acpModel })}
              />
            )}
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

      {/* MCP servers (both kinds): all configured, or an explicit subset */}
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

      {/* Secrets (both kinds): no restriction, or an explicit subset */}
      <div className="flex flex-col gap-2.5">
        <Typography.Text className="text-sm">
          {t(I18nKey.SETTINGS$AGENT_PROFILE_SECRETS)}
        </Typography.Text>
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
        {secretCatalog.length > 0 ? (
          <ProfileScopeList
            testId="agent-profile-secret"
            items={secretCatalog}
            selected={
              state.secretsMode === "custom"
                ? state.secretsSelected
                : secretCatalog.map(({ name }) => name)
            }
            isDisabled={isDisabled || state.secretsMode === "standard"}
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
            {t(I18nKey.SETTINGS$AGENT_PROFILE_SECRETS_NONE)}
          </Typography.Text>
        )}
        <Typography.Text className="text-xs text-tertiary-alt">
          {t(
            state.secretsMode === "custom"
              ? I18nKey.SETTINGS$AGENT_PROFILE_SECRETS_CHOOSE_HINT
              : I18nKey.SETTINGS$AGENT_PROFILE_SECRETS_ALL_HINT,
          )}
        </Typography.Text>
      </div>
    </div>
  );
}
