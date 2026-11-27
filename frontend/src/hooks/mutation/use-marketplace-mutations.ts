import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AxiosError } from "axios";
import SettingsService from "#/api/settings-service/settings-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { MarketplaceRegistration, SettingsEdit } from "#/types/settings";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { I18nKey } from "#/i18n/declaration";
import { SETTINGS_QUERY_KEYS } from "#/hooks/query/query-keys";
import { useMarketplaceSkills } from "./use-get-marketplace-skills";

type MarketplaceChange = {
  revision?: string | null;
} & (
  | {
      marketplaces: MarketplaceRegistration[];
      addition?: MarketplaceRegistration;
    }
  | {
      marketplaces: MarketplaceRegistration[];
      removal: MarketplaceRegistration;
    }
);

export function useMarketplaceMutation(scope: "org" | "personal") {
  const org = scope === "org";
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const marketplaceSkillsMutation = useMarketplaceSkills();
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEYS.all });

  return useMutation({
    mutationFn: async (change: MarketplaceChange) => {
      const { marketplaces, revision } = change;
      const removal = "removal" in change ? change.removal : undefined;
      const addition = "addition" in change ? change.addition : undefined;
      const edit: SettingsEdit = {
        path: ["registered_marketplaces"],
        op: removal ? "remove" : addition ? "extend" : "set",
        value: removal ? [removal] : addition ? [addition] : marketplaces,
      };
      if (org && !revision) {
        throw new Error("Reload settings before editing.");
      }
      if (!removal && marketplaces.length) {
        const preview =
          await marketplaceSkillsMutation.mutateAsync(marketplaces);
        if (preview.errors?.length) {
          throw new Error(`Validation failed: ${preview.errors.join(", ")}`);
        }
      }
      const payload = revision
        ? { settings_edits: [edit] }
        : { registered_marketplaces: marketplaces };
      if (org) {
        await organizationService.saveOrganizationAppSettings({
          ...payload,
          last_known_updated_at: revision,
        });
      } else {
        await SettingsService.saveSettings(
          revision
            ? {
                ...payload,
                member_settings_revision: revision,
              }
            : payload,
        );
      }
    },
    onSuccess: () => {
      displaySuccessToast(t(I18nKey.SETTINGS$SAVED));
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
    onError: (error: Error) => {
      if (error instanceof AxiosError && error.response?.status === 409)
        invalidate();
      const message =
        error instanceof AxiosError
          ? retrieveAxiosErrorMessage(error)
          : error.message;
      displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
    },
    meta: { disableToast: true },
  });
}
