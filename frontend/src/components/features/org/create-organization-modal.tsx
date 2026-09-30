import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { useCreateOrganization } from "#/hooks/mutation/use-create-organization";
import { useOrganizations } from "#/hooks/query/use-organizations";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";

interface CreateOrganizationModalProps {
  contactEmail?: string;
  contactName?: string;
  onClose: () => void;
}

export function CreateOrganizationModal({
  contactEmail,
  contactName: contactNameProp,
  onClose,
}: CreateOrganizationModalProps) {
  const { t } = useTranslation();
  const { data } = useOrganizations();
  const { mutate: createOrganization, isPending } = useCreateOrganization();
  const [name, setName] = useState("");
  const [contactName, setContactName] = useState(contactNameProp ?? "");
  const [email, setEmail] = useState(contactEmail ?? "");

  const inferredContactName =
    contactNameProp?.trim() ||
    data?.organizations.find((org) => org.is_personal)?.contact_name?.trim() ||
    "";

  useEffect(() => {
    if (!inferredContactName) {
      return;
    }
    setContactName((current) => current || inferredContactName);
  }, [inferredContactName]);

  const handleSubmit = () => {
    const trimmedName = name.trim();
    const trimmedContactName = contactName.trim();
    const trimmedEmail = email.trim();

    if (!trimmedName || !trimmedContactName || !trimmedEmail) {
      displayErrorToast(t(I18nKey.ORG$CREATE_ORGANIZATION_REQUIRED_FIELDS));
      return;
    }

    createOrganization(
      {
        name: trimmedName,
        contact_name: trimmedContactName,
        contact_email: trimmedEmail,
      },
      {
        onSuccess: () => {
          displaySuccessToast(t(I18nKey.ORG$CREATE_ORGANIZATION_SUCCESS));
          onClose();
        },
        onError: () => {
          displayErrorToast(t(I18nKey.ORG$CREATE_ORGANIZATION_ERROR));
        },
      },
    );
  };

  return (
    <OrgModal
      testId="create-organization-form"
      title={t(I18nKey.ORG$CREATE_ORGANIZATION)}
      description={t(I18nKey.ORG$CREATE_ORGANIZATION_DESCRIPTION)}
      primaryButtonText={t(I18nKey.ORG$CREATE_ORGANIZATION)}
      onPrimaryClick={handleSubmit}
      onClose={onClose}
      isLoading={isPending}
    >
      <div className="flex flex-col gap-3 w-full">
        <SettingsInput
          testId="create-organization-name"
          type="text"
          label={t(I18nKey.ORG$ORGANIZATION_NAME)}
          value={name}
          placeholder={t(I18nKey.ORG$ORGANIZATION_NAME_PLACEHOLDER)}
          onChange={setName}
        />
        <SettingsInput
          testId="create-organization-contact-name"
          type="text"
          label={t(I18nKey.ORG$CONTACT_NAME)}
          value={contactName}
          placeholder={t(I18nKey.ORG$CONTACT_NAME_PLACEHOLDER)}
          onChange={setContactName}
        />
        <SettingsInput
          testId="create-organization-contact-email"
          type="email"
          label={t(I18nKey.ORG$CONTACT_EMAIL)}
          value={email}
          placeholder={t(I18nKey.ORG$CONTACT_EMAIL_PLACEHOLDER)}
          onChange={setEmail}
        />
      </div>
    </OrgModal>
  );
}
