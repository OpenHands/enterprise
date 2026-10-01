import React from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { setSuperAdminSetupStepComplete } from "#/components/features/super-admin/super-admin-setup";
import { useCreateOrganization } from "#/hooks/mutation/use-create-organization";
import { I18nKey } from "#/i18n/declaration";
import {
  markSuperAdminNuxOrgDone,
  readSuperAdminNux,
  SUPER_ADMIN_NUX_LLM_PATH,
} from "#/utils/org/super-admin-nux";

const FALLBACK_ORG_NAME = "My Organization";

export default function SuperAdminInstallOrg() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { mutateAsync: createOrganization, isPending } = useCreateOrganization();
  const translatedDefault = t(I18nKey.SA_NUX$ORG_DEFAULT_NAME);
  const defaultName = translatedDefault.startsWith("SA_NUX$")
    ? FALLBACK_ORG_NAME
    : translatedDefault;
  const [name, setName] = React.useState(defaultName);
  const edited = React.useRef(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!edited.current && defaultName) {
      setName(defaultName);
    }
  }, [defaultName]);

  const trimmed = name.trim();
  const canSubmit = trimmed.length > 1 && !isPending;

  const onContinue = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) {
      setError(t(I18nKey.SA_NUX$ORG_INVALID));
      return;
    }
    const account = readSuperAdminNux().account;
    setError(null);
    try {
      await createOrganization({
        name: trimmed,
        contact_name: account?.name?.trim() || "Super Admin",
        contact_email: account?.email?.trim() || "admin@localhost",
      });
    } catch {
      setError(t(I18nKey.SA_NUX$ORG_ERROR));
      return;
    }
    setSuperAdminSetupStepComplete("create-org", true);
    markSuperAdminNuxOrgDone({ name: trimmed });
    navigate(SUPER_ADMIN_NUX_LLM_PATH);
  };

  return (
    <form
      className="flex w-full max-w-md flex-col items-center gap-6"
      data-testid="super-admin-install-org"
      onSubmit={onContinue}
    >
      <OpenHandsLogoWhite
        width={68}
        height={46}
        aria-label={t(I18nKey.BRANDING$OPENHANDS_LOGO)}
      />
      <div className="flex flex-col gap-2 text-center">
        <h1 className="text-2xl font-semibold text-white">
          {t(I18nKey.SA_NUX$ORG_TITLE)}
        </h1>
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SA_NUX$ORG_BODY)}
        </p>
      </div>

      <div className="w-full">
        <SettingsInput
          testId="sa-nux-org-name"
          name="orgName"
          label={t(I18nKey.SA_NUX$ORG_NAME_LABEL)}
          type="text"
          value={name}
          onChange={(value) => {
            edited.current = true;
            setName(value);
          }}
          placeholder={defaultName}
        />
      </div>

      {error && (
        <p className="w-full text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <BrandButton
        type="submit"
        variant="primary"
        className="w-full"
        testId="sa-nux-org-continue"
        isDisabled={!canSubmit}
      >
        {t(I18nKey.SA_NUX$NEXT)}
      </BrandButton>
    </form>
  );
}
