import { useState } from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useCreateOrganization } from "#/hooks/mutation/use-create-organization";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";

interface CreateOrganizationModalProps {
  contactEmail?: string;
  onClose: () => void;
}

export function CreateOrganizationModal({
  contactEmail,
  onClose,
}: CreateOrganizationModalProps) {
  const { t } = useTranslation();
  const { mutate: createOrganization, isPending } = useCreateOrganization();
  const [name, setName] = useState("");
  const [contactName, setContactName] = useState("");
  const [email, setEmail] = useState(contactEmail ?? "");

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
        <input
          data-testid="create-organization-name"
          value={name}
          placeholder={t(I18nKey.ORG$ORGANIZATION_NAME)}
          onChange={(e) => setName(e.target.value)}
          className="bg-tertiary border border-[#717888] h-10 w-full rounded-sm p-2 placeholder:italic placeholder:text-tertiary-alt"
        />
        <input
          data-testid="create-organization-contact-name"
          value={contactName}
          placeholder={t(I18nKey.ORG$CONTACT_NAME)}
          onChange={(e) => setContactName(e.target.value)}
          className="bg-tertiary border border-[#717888] h-10 w-full rounded-sm p-2 placeholder:italic placeholder:text-tertiary-alt"
        />
        <input
          data-testid="create-organization-contact-email"
          value={email}
          placeholder={t(I18nKey.ORG$CONTACT_EMAIL)}
          onChange={(e) => setEmail(e.target.value)}
          className="bg-tertiary border border-[#717888] h-10 w-full rounded-sm p-2 placeholder:italic placeholder:text-tertiary-alt"
        />
      </div>
    </OrgModal>
  );
}
