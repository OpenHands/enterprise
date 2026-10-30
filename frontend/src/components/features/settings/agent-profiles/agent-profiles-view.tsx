import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { FaChevronLeft } from "react-icons/fa6";
import { useTranslation } from "react-i18next";
import AgentProfilesService, {
  AgentProfile,
  AgentProfileSaveInput,
  AgentProfileScope,
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
import { useOrgSecrets, useSearchSecrets } from "#/hooks/query/use-get-secrets";
import { useCanManageOrgAgentProfiles } from "#/hooks/organizations/use-can-manage-org-agent-profiles";
import { useSkills } from "#/hooks/query/use-skills";
import { useToolCatalog } from "#/hooks/query/use-tool-catalog";
import SkillsService from "#/api/skills-service";
import type { MarketplaceRegistration } from "#/types/settings";
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

/**
 * The org editor's view of a profile's scopes: no MCP servers, and an explicit
 * secrets list -- every org-shared secret when the stored profile had no list
 * (OHE-3088). The save body enforces the same shape.
 */
function withOrgScopes(
  state: AgentProfileFormState,
  stored: AgentProfile | null,
  orgSecretNames: string[],
): AgentProfileFormState {
  return {
    ...state,
    mcpMode: "custom",
    mcpSelected: [],
    secretsMode: "custom",
    secretsSelected: Array.isArray(stored?.secret_refs)
      ? state.secretsSelected
      : orgSecretNames,
  };
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
 * The Agent Profiles page: the member's profile library with an in-place
 * create/edit editor, mirroring the Agent Canvas experience. Writes carry the
 * scope of the collection being edited: a member's own profiles, or -- for an
 * org admin with the org-profiles flag on -- the organization's shared ones,
 * which the editor narrows to organization-scoped settings (OHE-3088).
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
  const orgSecrets = useOrgSecrets();
  const { data: skills, isError: skillsLoadFailed } = useSkills();
  const saveProfile = useSaveAgentProfile();
  const renameProfile = useRenameAgentProfile();
  // The editor is seeded from the settings once, when it opens (the
  // agent-settings schema normally rides inside them).
  const isEditorReady = !!settings;

  const [viewMode, setViewMode] = useState<ViewMode>("list");
  // The collection the open editor writes to.
  const [editingScope, setEditingScope] =
    useState<AgentProfileScope>("personal");
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
  // Org admins manage the organization's shared profiles here when the
  // org-profiles flag is on; everyone else only sets one as their default.
  const canManageOrg = useCanManageOrgAgentProfiles();
  const isOrgEditor = editingScope === "org";
  // The org editor seeds its secrets list from the org's shared secrets, so
  // it also waits for that list to have loaded.
  const isOrgEditorReady =
    isEditorReady && !orgSecrets.isLoading && !orgSecrets.isError;
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
      profileScope: editingScope,
    }),
    [
      acpProviders,
      toolConcurrencyField,
      schema,
      defaultLlmProfileRef,
      editingScope,
    ],
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
  // An org profile may only reference the organization's shared secrets,
  // listed under the raw names the org endpoints use.
  const orgSecretItems = useMemo<ProfileScopeItem[]>(
    () =>
      orgSecrets.data.map((secret) => ({
        name: secret.name,
        description: secret.description ?? null,
        scope: "organization" as const,
      })),
    [orgSecrets.data],
  );
  const orgSecretNames = useMemo(
    () => orgSecretItems.map(({ name }) => name),
    [orgSecretItems],
  );

  // Skills with the scope of the marketplace they come from, as the Skills
  // page derives it; organization-marketplace skills get the badge.
  // Two lists, as on the Skills page: the server's global and user skills
  // (``useSkills``), plus the skills of every marketplace the member inherits
  // or registered. A marketplace skill takes its marketplace's scope, so
  // organization-marketplace skills get the badge. Fetched only while the
  // editor is open.
  const inheritedMarketplaces = settings?.inherited_marketplaces;
  const registeredMarketplaces = settings?.registered_marketplaces;
  // An org profile loads organization and instance skills only, so its
  // picker leaves out the member's own marketplaces and skills.
  const marketplaces = useMemo<MarketplaceRegistration[]>(
    () => [
      ...(inheritedMarketplaces ?? []),
      ...(isOrgEditor ? [] : (registeredMarketplaces ?? [])),
    ],
    [inheritedMarketplaces, registeredMarketplaces, isOrgEditor],
  );
  const { data: marketplaceSkills, isError: isMarketplaceSkillsError } =
    useQuery({
      queryKey: [
        "agent-profile-marketplace-skills",
        marketplaces.map(
          (mp) => `${mp.source}@${mp.ref ?? ""}:${mp.repo_path ?? ""}`,
        ),
      ],
      queryFn: () => SkillsService.getMarketplaceSkills(marketplaces),
      enabled: viewMode !== "list" && marketplaces.length > 0,
      staleTime: 1000 * 60 * 10,
      meta: { disableToast: true },
    });
  // The server's tool catalog, for the tools picker; fetched only while the
  // editor is open. Rows are the user-selectable tools, plus any pinned tool
  // the catalog knows but does not offer (so it shows, and can be unpinned,
  // without being reported as unknown).
  const {
    data: toolCatalog,
    isPending: isToolCatalogPending,
    isError: isToolCatalogError,
  } = useToolCatalog(viewMode !== "list");
  const selectedToolNames = formState?.toolsSelected;
  const toolItems = useMemo<ProfileScopeItem[]>(() => {
    const pinned = new Set(selectedToolNames ?? []);
    return (toolCatalog ?? [])
      .filter((entry) => entry.user_selectable || pinned.has(entry.name))
      .map((entry) => ({
        name: entry.name,
        description: entry.description || null,
      }));
  }, [toolCatalog, selectedToolNames]);
  const skillItems = useMemo<ProfileScopeItem[]>(() => {
    const items: ProfileScopeItem[] = [];
    const seen = new Set<string>();
    // An org profile loads organization and instance skills only: the
    // server's global skills and those of the inherited marketplaces, never
    // the member's own.
    const marketplaceSources = new Set(
      marketplaces.map((mp) => `marketplace:${mp.name}`),
    );
    const serverSkills = (skills ?? []).filter(
      (skill) =>
        !isOrgEditor ||
        skill.source === "global" ||
        marketplaceSources.has(skill.source),
    );
    for (const skill of serverSkills) {
      if (!seen.has(skill.name)) {
        seen.add(skill.name);
        items.push({ name: skill.name });
      }
    }
    for (const skill of marketplaceSkills?.skills ?? []) {
      if (!seen.has(skill.name)) {
        seen.add(skill.name);
        const marketplace = marketplaces.find(
          (mp) => skill.source === `marketplace:${mp.name}`,
        );
        items.push({
          name: skill.name,
          scope: marketplace?.scope === "org" ? "organization" : undefined,
        });
      }
    }
    return items;
  }, [skills, marketplaceSkills, marketplaces, isOrgEditor]);

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
    setEditingScope("personal");
    setEditingProfile(null);
    setProfileName("");
    setFormState(null);
    setInitialInput(null);
  }, []);

  const handleAddProfile = useCallback(
    (scope: AgentProfileScope) => {
      if (scope === "org" ? !isOrgEditorReady : !isEditorReady) return;
      const scopedDeps = { ...deps, profileScope: scope };
      let state = newAgentProfileFormState(scopedDeps);
      if (scope === "org") state = withOrgScopes(state, null, orgSecretNames);
      setEditingScope(scope);
      setEditingProfile(null);
      setProfileName("");
      setFormState(state);
      setInitialInput(null);
      setViewMode("create");
    },
    [deps, isEditorReady, isOrgEditorReady, orgSecretNames],
  );

  const handleEditProfile = useCallback(
    async (summary: AgentProfileSummary) => {
      // A summary without a scope (an older backend) is an org profile.
      const scope: AgentProfileScope =
        summary.scope === "personal" ? "personal" : "org";
      if (scope === "org" ? !isOrgEditorReady : !isEditorReady) return;
      const scopedDeps = { ...deps, profileScope: scope };
      try {
        const detail = await AgentProfilesService.getProfile(
          summary.name,
          scope,
        );
        const { profile } = detail;
        const storedState = agentProfileFormStateFromProfile(
          profile,
          scopedDeps,
        );
        let state = storedState;
        if (scope === "org") {
          state = withOrgScopes(state, profile, orgSecretNames);
        }
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
        setEditingScope(scope);
        setEditingProfile(profile);
        setProfileName(profile.name);
        setFormState(state);
        // Dirty-tracking compares against the profile as stored (hence no
        // org enforcement in these deps), so a healed ref, or the org
        // editor's shape forced onto a legacy org profile, counts as a
        // change the user can save.
        setInitialInput(
          snapshotInput(
            storedState,
            { ...scopedDeps, profileScope: undefined },
            profile,
          ),
        );
        setViewMode("edit");
      } catch (error) {
        const message = retrieveAxiosErrorMessage(error as AxiosError);
        displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
      }
    },
    [
      deps,
      isEditorReady,
      isOrgEditorReady,
      llmProfilesData,
      llmProfiles,
      defaultLlmProfileRef,
      orgSecretNames,
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
          scope: editingScope,
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
        scope: editingScope,
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
        isAddOrgDisabled={!isOrgEditorReady}
        canManageOrg={canManageOrg}
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
  // An org profile saved before the org editor existed still lets members
  // use their own secrets and MCP servers; saving it here changes that.
  const isLegacyOrgProfile =
    isOrgEditor &&
    viewMode === "edit" &&
    !!editingProfile &&
    (!Array.isArray(editingProfile.mcp_server_refs) ||
      !Array.isArray(editingProfile.secret_refs));

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
        {isOrgEditor ? (
          <p
            data-testid="org-agent-profile-editor-hint"
            className="text-sm leading-5 text-muted"
          >
            {t(I18nKey.SETTINGS$ORG_AGENT_PROFILE_EDITOR_HINT)}
          </p>
        ) : null}
        {isLegacyOrgProfile ? (
          <p
            data-testid="org-agent-profile-legacy-warning"
            className="text-sm leading-5 text-yellow-400"
          >
            {t(I18nKey.SETTINGS$ORG_AGENT_PROFILE_LEGACY_WARNING)}
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
        tools={toolItems}
        toolsLoading={isToolCatalogPending}
        toolsLoadFailed={isToolCatalogError}
        mcpServers={mcpServers}
        secrets={isOrgEditor ? orgSecretItems : secretItems}
        skills={skillItems}
        skillsLoadFailed={skillsLoadFailed || isMarketplaceSkillsError}
        credentialForm={credentialForm}
        profileScope={editingScope}
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
