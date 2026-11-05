import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useConfig } from "#/hooks/query/use-config";
import { useAuthUrl } from "#/hooks/use-auth-url";
import { settingsListRowActionButtonClassName } from "#/utils/settings-list-classes";
import { BrandButton } from "../brand-button";

export function ConfigureGitLabAnchor() {
  const { t } = useTranslation();
  const { data: config } = useConfig();

  const authUrl = useAuthUrl({
    appMode: config?.app_mode ?? null,
    identityProvider: "gitlab",
    authUrl: config?.auth_url,
  });

  const handleOAuthFlow = () => {
    if (!authUrl) {
      return;
    }

    window.location.href = authUrl;
  };

  return (
    <BrandButton
      testId="configure-gitlab-button"
      type="button"
      variant="primary"
      className={settingsListRowActionButtonClassName}
      onClick={handleOAuthFlow}
    >
      {t(I18nKey.SETTINGS$CONNECT)}
    </BrandButton>
  );
}
