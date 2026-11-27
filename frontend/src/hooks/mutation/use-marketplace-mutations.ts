import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AxiosError } from "axios";
import SettingsService from "#/api/settings-service/settings-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import {
  MarketplaceRegistration,
  Settings,
  SettingsEdit,
} from "#/types/settings";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { I18nKey } from "#/i18n/declaration";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import {
  ORGANIZATION_APP_SETTINGS_KEYS,
  SETTINGS_QUERY_KEYS,
} from "#/hooks/query/query-keys";
import { useMarketplaceSkills } from "./use-get-marketplace-skills";

function savePersonalMarketplaces(
  settings: Pick<Settings, "member_settings_revision">,
  edit: SettingsEdit,
  fallback: MarketplaceRegistration[],
) {
  return SettingsService.saveSettings(
    settings.member_settings_revision
      ? {
          member_settings_revision: settings.member_settings_revision,
          settings_edits: [edit],
        }
      : { registered_marketplaces: fallback },
  );
}

type PersonalMarketplaceSave = {
  marketplaces: MarketplaceRegistration[];
  revision?: string | null;
  addition?: MarketplaceRegistration;
};

type PersonalMarketplaceDelete = {
  marketplaceName: string;
  settings: Pick<
    Settings,
    "member_settings_revision" | "registered_marketplaces"
  >;
};

export function useMarketplaceMutations() {
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { organizationId } = useSelectedOrganizationId();
  const marketplaceSkillsMutation = useMarketplaceSkills();
  const invalidate = (org: boolean) => {
    if (org)
      queryClient.invalidateQueries({
        queryKey: ORGANIZATION_APP_SETTINGS_KEYS.byOrg(organizationId),
      });
    queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEYS.all });
  };
  const callbacks = (org: boolean) => ({
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SETTINGS$SAVED));
      invalidate(org);
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (error: Error) => {
      if (
        org &&
        error instanceof AxiosError &&
        error.response?.status === 409
      ) {
        invalidate(org);
        displayErrorToast("Settings were modified. Please retry.");
        return;
      }
      const message =
        error instanceof AxiosError
          ? retrieveAxiosErrorMessage(error)
          : error.message;
      displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
    },
    meta: { disableToast: true },
  });
  const previewMarketplaces = async (
    marketplaces: MarketplaceRegistration[],
  ) => {
    if (!marketplaces.length) return;
    const preview = await marketplaceSkillsMutation.mutateAsync(marketplaces);
    if (preview.errors?.length) {
      throw new Error(`Validation failed: ${preview.errors.join(", ")}`);
    }
  };

  const savePersonal = useMutation({
    mutationFn: async ({
      marketplaces,
      revision,
      addition,
    }: PersonalMarketplaceSave) => {
      await previewMarketplaces(marketplaces);
      await savePersonalMarketplaces(
        { member_settings_revision: revision },
        {
          path: ["registered_marketplaces"],
          op: addition ? "extend" : "set",
          value: addition ? [addition] : marketplaces,
        },
        marketplaces,
      );
    },
    ...callbacks(false),
  });

  const saveOrg = useMutation({
    mutationFn: async ({
      marketplaces,
      lastKnownUpdatedAt,
      addition,
    }: {
      addition?: MarketplaceRegistration;
      marketplaces: MarketplaceRegistration[];
      lastKnownUpdatedAt: string | null;
    }) => {
      await previewMarketplaces(marketplaces);
      // Backend will automatically set scope='org' for org settings
      return organizationService.saveOrganizationAppSettings({
        ...(lastKnownUpdatedAt
          ? {
              settings_edits: [
                {
                  path: ["registered_marketplaces"],
                  op: addition ? "extend" : "set",
                  value: addition ? [addition] : marketplaces,
                },
              ],
            }
          : { registered_marketplaces: marketplaces }),
        last_known_updated_at: lastKnownUpdatedAt,
      });
    },
    ...callbacks(true),
  });

  const deletePersonal = useMutation({
    mutationFn: ({ marketplaceName, settings }: PersonalMarketplaceDelete) =>
      savePersonalMarketplaces(
        settings,
        {
          path: ["registered_marketplaces"],
          op: "remove",
          value: (settings.registered_marketplaces ?? []).filter(
            (mp) => mp.name === marketplaceName,
          ),
        },
        (settings.registered_marketplaces ?? []).filter(
          (mp) => mp.name !== marketplaceName,
        ),
      ),
    ...callbacks(false),
  });

  const deleteOrg = useMutation({
    mutationFn: async ({
      marketplace,
      lastKnownUpdatedAt,
    }: {
      marketplace: MarketplaceRegistration;
      lastKnownUpdatedAt: string | null;
    }) => {
      if (!lastKnownUpdatedAt)
        throw new Error("Reload settings before deleting.");
      return organizationService.saveOrganizationAppSettings({
        settings_edits: [
          {
            path: ["registered_marketplaces"],
            op: "remove",
            value: [marketplace],
          },
        ],
        last_known_updated_at: lastKnownUpdatedAt,
      });
    },
    ...callbacks(true),
  });

  return { savePersonal, saveOrg, deletePersonal, deleteOrg };
}
