import { isAxiosError } from "axios";
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
  delete settingsToSave.agent_settings_schema;
  delete settingsToSave.conversation_settings_schema;

  const agentSettings = settingsToSave.agent_settings;
  const llm = agentSettings?.llm;
  if (
    llm &&
    typeof llm === "object" &&
    !Array.isArray(llm) &&
    typeof llm.api_key === "string"
  ) {
    settingsToSave.agent_settings = {
      ...agentSettings,
      llm: { ...llm, api_key: llm.api_key.trim() },
    };
  }

  if (typeof settingsToSave.search_api_key === "string") {
    settingsToSave.search_api_key = settingsToSave.search_api_key.trim();
  }
  if (typeof settingsToSave.git_user_name === "string") {
    settingsToSave.git_user_name = settingsToSave.git_user_name.trim();
  }
  if (typeof settingsToSave.git_user_email === "string") {
    settingsToSave.git_user_email = settingsToSave.git_user_email.trim();
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
    onError: async (error) => {
      if (isAxiosError(error) && error.response?.status === 409) {
        await queryClient.invalidateQueries({
          queryKey: SETTINGS_QUERY_KEYS.byScope(scope, organizationId),
        });
      }
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
