import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { InteractiveOpenHandsIcon } from "#/components/features/setup/interactive-openhands-icon";
import { BrandButton } from "#/components/features/settings/brand-button";
import { I18nKey } from "#/i18n/declaration";
import { markSuperAdminNuxWelcomeDone } from "#/utils/org/super-admin-nux";
import "./super-admin-install-welcome.css";

export default function SuperAdminInstallWelcome() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const onNext = () => {
    markSuperAdminNuxWelcomeDone();
    navigate("/install/tos");
  };

  return (
    <div
      className="flex w-full max-w-lg flex-col items-center gap-8 text-center"
      data-testid="super-admin-install-welcome"
    >
      <InteractiveOpenHandsIcon
        className="oh-welcome-icon-enter"
        label={t(I18nKey.BRANDING$OPENHANDS_LOGO)}
      />
      <div className="oh-welcome-blur-in oh-welcome-blur-in--delay-1 flex flex-col gap-3">
        <h1 className="text-3xl font-normal leading-tight text-white sm:text-4xl">
          {t(I18nKey.SA_NUX$WELCOME_TITLE)}
        </h1>
        <p className="text-base leading-6 text-[var(--oh-muted)]">
          {t(I18nKey.SA_NUX$WELCOME_BODY)}
        </p>
      </div>
      <div className="oh-welcome-blur-in oh-welcome-blur-in--delay-2">
        <BrandButton
          type="button"
          variant="primary"
          className="min-w-[160px]"
          testId="sa-nux-welcome-next"
          onClick={onNext}
        >
          {t(I18nKey.SA_NUX$NEXT)}
        </BrandButton>
      </div>
    </div>
  );
}
