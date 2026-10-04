import React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { organizationService } from "#/api/organization-service/organization-service.api";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { setSuperAdminSetupStepComplete } from "#/components/features/super-admin/super-admin-setup";
import { useUpdateSetupState } from "#/hooks/mutation/use-super-admin-mutations";
import { I18nKey } from "#/i18n/declaration";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { setSelectedOrg } from "#/utils/local-storage";
import {
  markSuperAdminNuxOrgDone,
  SUPER_ADMIN_NUX_LLM_PATH,
} from "#/utils/org/super-admin-nux";

const FALLBACK_ORG_NAME = "My Organization";

export default function SuperAdminInstallOrg() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { mutateAsync: updateSetupState } = useUpdateSetupState();
  const translatedDefault = t(I18nKey.SA_NUX$ORG_DEFAULT_NAME);
  const defaultName = translatedDefault.startsWith("SA_NUX$")
    ? FALLBACK_ORG_NAME
    : translatedDefault;
  const [name, setName] = React.useState(defaultName);
  const edited = React.useRef(false);
  const [error, setError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  React.useEffect(() => {
    if (!edited.current && defaultName) {
      setName(defaultName);
    }
  }, [defaultName]);

  const trimmed = name.trim();
  const canSubmit = trimmed.length > 1 && !isSubmitting;

  const onContinue = async (event: React.FormEvent) => {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }
    if (!canSubmit) {
      setError(t(I18nKey.SA_NUX$ORG_INVALID));
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      const { items } = await organizationService.getOrganizations();
      // The signed-in user's name and email live on their personal workspace,
      // which has the same id as the user.
      const personal = items.find((org) => org.is_personal);
      // A team organization the Super Admin already belongs to (the default-org
      // bootstrap's, or one an earlier attempt created) is renamed, not duplicated.
      const existing = items.find((org) => !org.is_personal);
      const org = existing
        ? await organizationService.updateOrganization({
            orgId: existing.id,
            name: trimmed,
          })
        : await organizationService.createOrganization({
            name: trimmed,
            contact_name: personal?.contact_name?.trim() || "Super Admin",
            contact_email: personal?.contact_email?.trim() || "admin@localhost",
            owner_user_id: personal?.id,
          });
      // Open the org LLM defaults in this organization. The server refuses the
      // switch unless the Super Admin is a member.
      await organizationService.switchOrganization({ orgId: org.id });
      useSelectedOrganizationStore.getState().setOrganizationId(org.id);
      setSelectedOrg(org.id);
      await queryClient.invalidateQueries({ queryKey: ["organizations"] });
      // This is the wizard's last step; finishing it here ends it on every browser.
      await updateSetupState({ wizard_completed: true });
    } catch {
      setError(t(I18nKey.SA_NUX$ORG_ERROR));
      setIsSubmitting(false);
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
