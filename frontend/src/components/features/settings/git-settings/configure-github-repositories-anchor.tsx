import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { settingsListRowActionButtonClassName } from "#/utils/settings-list-classes";
import { BrandButton } from "../brand-button";

interface ConfigureGitHubRepositoriesAnchorProps {
  slug: string;
}

export function ConfigureGitHubRepositoriesAnchor({
  slug,
}: ConfigureGitHubRepositoriesAnchorProps) {
  const { t } = useTranslation();

  return (
    <BrandButton
      testId="configure-github-repositories-button"
      type="button"
      variant="primary"
      className={settingsListRowActionButtonClassName}
      onClick={() =>
        window.open(
          `https://github.com/apps/${slug}/installations/new`,
          "_blank",
          "noreferrer noopener",
        )
      }
    >
      {t(I18nKey.GITHUB$CONFIGURE_REPOS)}
    </BrandButton>
  );
}
