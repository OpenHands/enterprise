import { useCallback, useEffect, useMemo, useState } from "react";
import { AxiosError } from "axios";
import { FaChevronLeft } from "react-icons/fa6";
import { useTranslation } from "react-i18next";
import AgentProfilesService, {
  AgentProfile,
  AgentProfileSaveInput,
  AgentProfileSummary,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import type { ACPProviderConfig } from "#/api/option-service/option.types";
import { BrandButton } from "#/components/features/settings/brand-button";
import { ProfileNameInput } from "#/components/features/settings/profile-name-input";
import { useRenameAgentProfile } from "#/hooks/mutation/use-rename-agent-profile";
import { useSaveAgentProfile } from "#/hooks/mutation/use-save-agent-profile";
import { useAgentProfiles } from "#/hooks/query/use-agent-profiles";
import { useAgentSettingsSchema } from "#/hooks/query/use-agent-settings-schema";
import { useConfig } from "#/hooks/query/use-config";
import { useSearchSecrets } from "#/hooks/query/use-get-secrets";
import { useLlmProfiles } from "#/hooks/query/use-llm-profiles";
import { useSettings } from "#/hooks/query/use-settings";
import { useAcpCredentialForm } from "#/hooks/use-acp-credential-form";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import { CUSTOM_PRESET, detectPreset } from "#/utils/acp-agent-form";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { formControlBackNavButtonClassName } from "#/utils/form-control-classes";
import { parseMcpConfig } from "#/utils/mcp-config";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { AgentProfileForm } from "./agent-profile-form";
import {
  AGENT_PROFILE_NAME_PATTERN,
  AgentProfileFormDeps,
  AgentProfileFormState,
  agentProfileFormStateFromProfile,
  buildAgentProfileSaveInput,
  isAgentProfileFormValid,
  newAgentProfileFormState,
} from "./agent-profile-form-state";
import { AgentProfilesManager } from "./agent-profiles-manager";
import { mergeAgentProfileSaveInput } from "./merge-agent-profile-save-input";
import { ProfileScopeItem } from "./profile-scope-list";

type ViewMode = "list" | "create" | "edit";

const EMPTY_ACP_PROVIDERS: ACPProviderConfig[] = [];
/** The member's configured MCP servers, keyed the way the backend filters them. */
function configuredMcpServers(raw: unknown): ProfileScopeItem[] {
  const config = parseMcpConfig(raw);
  const servers: unknown[] = [
    ...config.sse_servers,
    ...config.stdio_servers,
    ...config.shttp_servers,
  ];
  const items: ProfileScopeItem[] = [];
  for (const server of servers) {
    if (server && typeof server === "object") {
      const { name, type } = server as { name?: unknown; type?: unknown };
      if (typeof name === "string") {
        items.push({
          name,
          description: typeof type === "string" ? type : null,
        });
      }
    }
  }
  return items;
}

/** Serialized save body, or ``null`` while the form holds an invalid value. */
function snapshotInput(
  state: AgentProfileFormState,
  deps: AgentProfileFormDeps,
  stored?: AgentProfile | null,
): string | null {
  try {
    return JSON.stringify(buildAgentProfileSaveInput(state, deps, stored));
  } catch {
    return null;
  }
}

/**
 * The personal Agent Profiles page: the member's profile library with an
 * in-place create/edit editor, mirroring the Agent Canvas experience. Every
 * write targets ``scope=personal``; the org's shared profiles are listed for
 * activation only.
 */
export function AgentProfilesView() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { data: settings } = useSettings();
  const { data: schema } = useAgentSettingsSchema(
    settings?.agent_settings_schema,
  );
  const { data: profilesData } = useAgentProfiles();
  const { data: llmProfilesData } = useLlmProfiles();
  // Same query as the ACP credential form, so the list is fetched once.
  const secretsQuery = useSearchSecrets();
  const saveProfile = useSaveAgentProfile();
  const renameProfile = useRenameAgentProfile();
  // The editor is seeded from the settings once, when it opens (the
  // agent-settings schema normally rides inside them).
  const isEditorReady = !!settings;

  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [profileName, setProfileName] = useState("");
  const [editingProfile, setEditingProfile] = useState<AgentProfile | null>(
    null,
  );
  const [formState, setFormState] = useState<AgentProfileFormState | null>(
    null,
  );
  const [initialInput, setInitialInput] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  // The Secrets picker lists every saved secret, so page through them all.
  const {
    hasNextPage,
    isFetchingNextPage,
    isError: isSecretsError,
    fetchNextPage,
  } = secretsQuery;
  useEffect(() => {
    // A failed page leaves hasNextPage true; don't loop on it.
    if (
      viewMode !== "list" &&
      hasNextPage &&
      !isFetchingNextPage &&
      !isSecretsError
    ) {
      fetchNextPage();
    }
  }, [
    viewMode,
    hasNextPage,
    isFetchingNextPage,
    isSecretsError,
    fetchNextPage,
  ]);

  const acpProviders = config?.acp_providers ?? EMPTY_ACP_PROVIDERS;
  const isAcpEnabled = !!config?.feature_flags?.enable_acp;
  const fields = useMemo(
    () => schema?.sections.flatMap((section) => section.fields),
    [schema],
  );
  const toolConcurrencyField = fields?.find(
    (field) => field.key === "tool_concurrency_limit",
  );
  const llmProfiles = llmProfilesData?.profiles ?? [];
  const defaultLlmProfileRef =
    llmProfilesData?.active_profile ?? llmProfiles[0]?.name ?? "";
  const deps = useMemo<AgentProfileFormDeps>(
    () => ({
      acpProviders,
      toolConcurrencyField,
      schema: schema ?? null,
      defaultLlmProfileRef,
    }),
    [acpProviders, toolConcurrencyField, schema, defaultLlmProfileRef],
  );
  const mcpConfigRaw = settings?.agent_settings?.mcp_config;
  const mcpServers = useMemo(
    () => configuredMcpServers(mcpConfigRaw),
    [mcpConfigRaw],
  );
  // The search list carries personal and organization-shared secrets under
  // the names the runtime exposes, so ``secret_refs`` can reference either.
  const secretItems = useMemo<ProfileScopeItem[]>(
    () =>
      secretsQuery.data.map((secret) => ({
        name: secret.name,
        description: secret.description ?? null,
        scope: secret.scope,
      })),
    [secretsQuery.data],
  );

  // ACP credentials are global secrets keyed by the selected preset.
  const selectedPreset =
    formState?.agentKind === "acp"
      ? detectPreset(formState.acpCommandText, acpProviders)
      : null;
  const credentialPreset =
    selectedPreset && selectedPreset !== CUSTOM_PRESET ? selectedPreset : null;
  const credentialForm = useAcpCredentialForm(
    credentialPreset,
    acpProviders.find(({ key }) => key === credentialPreset),
  );

  // ── Name validation ──────────────────────────────────────────────────────
  const existingNames = useMemo(
    () => new Set(profilesData?.profiles.map((profile) => profile.name) ?? []),
    [profilesData],
  );
  const trimmedName = profileName.trim();
  const isNameFormatValid = AGENT_PROFILE_NAME_PATTERN.test(trimmedName);
  const isDuplicateName =
    isNameFormatValid &&
    existingNames.has(trimmedName) &&
    (viewMode === "create" || trimmedName !== editingProfile?.name);
  const isNameValid = isNameFormatValid && !isDuplicateName;

  // ── Navigation between list and editor ───────────────────────────────────
  const handleBackToList = useCallback(() => {
    setViewMode("list");
    setEditingProfile(null);
    setProfileName("");
    setFormState(null);
    setInitialInput(null);
  }, []);

  const handleAddProfile = useCallback(() => {
    if (!isEditorReady) return;
    setEditingProfile(null);
    setProfileName("");
    setFormState(newAgentProfileFormState(deps));
    setInitialInput(null);
    setViewMode("create");
  }, [deps, isEditorReady]);

  const handleEditProfile = useCallback(
    async (summary: AgentProfileSummary) => {
      if (!isEditorReady) return;
      try {
        const detail = await AgentProfilesService.getProfile(
          summary.name,
          "personal",
        );
        const { profile } = detail;
        const storedState = agentProfileFormStateFromProfile(profile, deps);
        let state = storedState;
        if (profile.agent_kind === "openhands") {
          // A profile can reference an LLM profile that has since been
          // deleted; fall back to the default so a dangling ref self-heals
          // on load instead of being saved straight back.
          // Only against a loaded list: a pending or failed fetch is no
          // evidence that the ref is gone.
          const refIsLive =
            llmProfilesData === undefined ||
            llmProfiles.some(({ name }) => name === profile.llm_profile_ref);
          if (!refIsLive) {
            state = { ...state, llmProfileRef: defaultLlmProfileRef };
          }
        }
        setEditingProfile(profile);
        setProfileName(profile.name);
        setFormState(state);
        // Dirty-tracking compares against what is stored, so a healed ref
        // counts as a change the user can save.
        setInitialInput(snapshotInput(storedState, deps, profile));
        setViewMode("edit");
      } catch (error) {
        const message = retrieveAxiosErrorMessage(error as AxiosError);
        displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
      }
    },
    [
      deps,
      isEditorReady,
      llmProfilesData,
      llmProfiles,
      defaultLlmProfileRef,
      t,
    ],
  );

  // ── Save ─────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!formState || !isNameValid) return;

    let input: AgentProfileSaveInput;
    try {
      input = buildAgentProfileSaveInput(formState, deps, editingProfile);
    } catch (error) {
      displayErrorToast(
        error instanceof Error ? error.message : t(I18nKey.ERROR$GENERIC),
      );
      return;
    }
    if (input.agent_kind === "openhands" && !input.llm_profile_ref) {
      displayErrorToast(t(I18nKey.SETTINGS$AGENT_PROFILE_LLM_REQUIRED));
      return;
    }

    const originalName = editingProfile?.name;
    const isRename =
      viewMode === "edit" && !!originalName && originalName !== trimmedName;

    setIsSaving(true);
    try {
      // Persist ACP credentials (global secrets) before the profile spec.
      if (formState.agentKind === "acp" && credentialForm.isDirty) {
        const ok = await credentialForm.save({ silent: true });
        if (!ok) return;
        credentialForm.reset();
      }

      // Rename first (preserves the profile's stable id / active pointer);
      // saving under a new name would otherwise mint a fresh profile.
      let stored = viewMode === "edit" ? editingProfile : null;
      if (isRename && originalName) {
        await renameProfile.mutateAsync({
          name: originalName,
          newName: trimmedName,
          scope: "personal",
        });
        // The rename is committed even if the save below fails: remember the
        // new name so a retry saves instead of renaming again.
        stored = stored ? { ...stored, name: trimmedName } : stored;
        setEditingProfile(stored);
      }

      // Save is a whole-profile overwrite: keep the stored fields this editor
      // doesn't model.
      const profile = mergeAgentProfileSaveInput(stored, input);
      await saveProfile.mutateAsync({
        name: trimmedName,
        profile,
        scope: "personal",
      });

      displaySuccessToast(
        viewMode === "create"
          ? t(I18nKey.SETTINGS$PROFILE_CREATED, { name: trimmedName })
          : t(I18nKey.SETTINGS$PROFILE_UPDATED, { name: trimmedName }),
      );
      handleBackToList();
    } catch (error) {
      const message = retrieveAxiosErrorMessage(error as AxiosError);
      displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
    } finally {
      setIsSaving(false);
    }
  };

  // ── Render ───────────────────────────────────────────────────────────────
  if (viewMode === "list" || !formState) {
    return (
      <AgentProfilesManager
        onAddProfile={handleAddProfile}
        onEditProfile={handleEditProfile}
        isAddDisabled={!isEditorReady}
      />
    );
  }

  const isFormValid = isAgentProfileFormValid(formState);
  const nameDirty = viewMode === "edit" && trimmedName !== editingProfile?.name;
  const formDirty =
    viewMode === "edit" &&
    snapshotInput(formState, deps, editingProfile) !== initialInput;
  const hasUnsavedChanges =
    viewMode === "create" || nameDirty || formDirty || credentialForm.isDirty;

  return (
    <div data-testid="agent-profile-editor" className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <button
          data-testid="back-to-agent-profiles"
          type="button"
          onClick={handleBackToList}
          className={formControlBackNavButtonClassName}
        >
          <FaChevronLeft size={12} aria-hidden="true" />
          {t(I18nKey.BUTTON$BACK)}
        </button>
        <Typography.H2 testId="agent-profile-editor-title">
          {viewMode === "edit"
            ? t(I18nKey.SETTINGS$EDIT_AGENT_PROFILE)
            : t(I18nKey.SETTINGS$ADD_AGENT_PROFILE)}
        </Typography.H2>
        {viewMode === "create" ? (
          <p className="text-sm leading-5 text-muted">
            {t(I18nKey.SETTINGS$AGENT_PROFILE_SAVE_HINT)}
          </p>
        ) : null}
      </div>

      <div className="flex flex-col gap-1">
        <ProfileNameInput
          testId="agent-profile-name-input"
          value={profileName}
          onChange={setProfileName}
          isDisabled={isSaving}
          pattern={AGENT_PROFILE_NAME_PATTERN}
        />
        {isDuplicateName ? (
          <Typography.Paragraph
            testId="agent-profile-name-taken"
            className="text-xs text-red-400"
          >
            {t(I18nKey.SETTINGS$AGENT_PROFILE_NAME_TAKEN)}
          </Typography.Paragraph>
        ) : null}
      </div>

      <AgentProfileForm
        state={formState}
        onChange={(patch) =>
          setFormState((prev) => (prev ? { ...prev, ...patch } : prev))
        }
        isDisabled={isSaving}
        isAcpEnabled={isAcpEnabled}
        acpProviders={acpProviders}
        llmProfiles={llmProfiles}
        toolConcurrencyField={toolConcurrencyField}
        schema={schema ?? null}
        mcpServers={mcpServers}
        secrets={secretItems}
        credentialForm={credentialForm}
      />

      <div className="flex justify-start gap-3 pt-4">
        <BrandButton
          testId="cancel-agent-profile-btn"
          type="button"
          variant="secondary"
          onClick={handleBackToList}
          isDisabled={isSaving}
        >
          {t(I18nKey.BUTTON$CANCEL)}
        </BrandButton>
        <BrandButton
          testId="save-agent-profile-btn"
          type="button"
          variant="primary"
          onClick={handleSave}
          isDisabled={
            !isNameValid || !isFormValid || isSaving || !hasUnsavedChanges
          }
          aria-busy={isSaving}
        >
          {isSaving ? t(I18nKey.SETTINGS$SAVING) : t(I18nKey.BUTTON$SAVE)}
        </BrandButton>
      </div>
    </div>
  );
}
