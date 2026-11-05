import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { settingsListRowActionButtonClassName } from "#/utils/settings-list-classes";
import { BrandButton } from "../brand-button";

export function InstallSlackAppAnchor() {
  const { t } = useTranslation();

  return (
    <BrandButton
      testId="install-slack-app-button"
      type="button"
      variant="primary"
      className={settingsListRowActionButtonClassName}
      onClick={() =>
        window.open("/slack/install", "_blank", "noreferrer noopener")
      }
    >
      {t(I18nKey.SETTINGS$INSTALL)}
    </BrandButton>
  );
}
