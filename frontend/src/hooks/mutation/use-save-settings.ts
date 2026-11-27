import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { organizationService } from "#/api/organization-service/organization-service.api";
import SettingsService from "#/api/settings-service/settings-service.api";
import { Settings, SettingsScope } from "#/types/settings";
import { SETTINGS_QUERY_KEYS } from "../query/query-keys";

type SettingsUpdate = Partial<Settings> & Record<string, unknown>;

const saveSettingsMutationFn = async (
  scope: SettingsScope,
  settings: SettingsUpdate,
  organizationId?: string | null,
) => {
  const settingsToSave: SettingsUpdate = { ...settings };
  for (const block of ["agent_settings", "conversation_settings"] as const) {
    delete settingsToSave[block];
    delete settingsToSave[`${block}_schema`];
    const key = `${block}_diff`;
    if (!Object.keys(settingsToSave[key] ?? {}).length)
      delete settingsToSave[key];
  }
  const agent = settingsToSave.agent_settings_diff as
    | {
        llm?: Record<string, unknown>;
      }
    | undefined;
  if (typeof agent?.llm?.api_key === "string") {
    settingsToSave.agent_settings_diff = {
      ...agent,
      llm: { ...agent.llm, api_key: agent.llm.api_key.trim() },
    };
  }
  for (const key of [
    "search_api_key",
    "git_user_name",
    "git_user_email",
  ] as const) {
    const value = settingsToSave[key];
    if (typeof value === "string") settingsToSave[key] = value.trim();
  }

  if (scope === "org") {
    if (!organizationId) {
      throw new Error("Organization ID is required for org settings saves");
    }

    await organizationService.saveOrganizationSettings({
      orgId: organizationId,
      settings: settingsToSave,
    });
    return;
  }

  await SettingsService.saveSettings(settingsToSave);
};

export const useSaveSettings = (scope: SettingsScope = "personal") => {
  const queryClient = useQueryClient();
  const { organizationId } = useSelectedOrganizationId();

  return useMutation({
    mutationFn: async (settings: SettingsUpdate) => {
      await saveSettingsMutationFn(scope, settings, organizationId);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: SETTINGS_QUERY_KEYS.byScope(scope, organizationId),
      });
      if (scope === "org") {
        await queryClient.invalidateQueries({
          queryKey: SETTINGS_QUERY_KEYS.personal(organizationId),
        });
      }
    },
    meta: {
      disableToast: true,
    },
  });
};
