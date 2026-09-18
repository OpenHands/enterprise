import { useState } from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { I18nKey } from "#/i18n/declaration";
import { useUpdateOrganization } from "#/hooks/mutation/use-update-organization";
import { SettingsInput } from "#/components/features/settings/settings-input";

interface ChangeOrgNameModalProps {
  onClose: () => void;
}

export function ChangeOrgNameModal({ onClose }: ChangeOrgNameModalProps) {
  const { t } = useTranslation();
  const { mutate: updateOrganization, isPending } = useUpdateOrganization();
  const [orgName, setOrgName] = useState<string>("");

  const handleSubmit = () => {
    if (orgName?.trim()) {
      updateOrganization(orgName, {
        onSuccess: () => {
          onClose();
        },
      });
    }
  };

  return (
    <OrgModal
      testId="update-org-name-form"
      title={t(I18nKey.ORG$CHANGE_ORG_NAME)}
      description={t(I18nKey.ORG$MODIFY_ORG_NAME_DESCRIPTION)}
      primaryButtonText={t(I18nKey.BUTTON$SAVE)}
      onPrimaryClick={handleSubmit}
      onClose={onClose}
      isLoading={isPending}
    >
      <SettingsInput
        testId="org-name"
        type="text"
        label={t(I18nKey.ORG$ORGANIZATION_NAME)}
        value={orgName}
        placeholder={t(I18nKey.ORG$ENTER_NEW_ORGANIZATION_NAME)}
        onChange={setOrgName}
      />
    </OrgModal>
  );
}
