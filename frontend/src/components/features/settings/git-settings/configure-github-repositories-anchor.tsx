import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import SettingsGearIcon from "#/icons/settings-gear.svg?react";
import { settingsListRowActionButtonClassName } from "#/utils/settings-list-classes";
import { BrandButton } from "../brand-button";

interface ConfigureGitHubRepositoriesAnchorProps {
  slug: string;
  isInstalled?: boolean;
}

export function ConfigureGitHubRepositoriesAnchor({
  slug,
  isInstalled = false,
}: ConfigureGitHubRepositoriesAnchorProps) {
  const { t } = useTranslation();

  return (
    <BrandButton
      testId="configure-github-repositories-button"
      type="button"
      variant={isInstalled ? "secondary" : "primary"}
      className={settingsListRowActionButtonClassName}
      startContent={
        isInstalled ? (
          <SettingsGearIcon width={12} height={12} aria-hidden />
        ) : undefined
      }
      onClick={() =>
        window.open(
          `https://github.com/apps/${slug}/installations/new`,
          "_blank",
          "noreferrer noopener",
        )
      }
    >
      {t(I18nKey.PROJECT_MANAGEMENT$CONFIGURE_BUTTON_LABEL)}
    </BrandButton>
  );
}
