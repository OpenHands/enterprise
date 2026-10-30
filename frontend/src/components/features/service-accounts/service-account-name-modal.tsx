import React from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { I18nKey } from "#/i18n/declaration";

interface ServiceAccountNameModalProps {
  /** Set to rename an existing service account; omit to create one. */
  currentName?: string;
  onSubmit: (name: string) => void;
  onClose: () => void;
  isLoading?: boolean;
}

/** Name form shared by "create" and "rename" a service account. */
export function ServiceAccountNameModal({
  currentName,
  onSubmit,
  onClose,
  isLoading = false,
}: ServiceAccountNameModalProps) {
  const { t } = useTranslation();
  const [name, setName] = React.useState(currentName ?? "");
  const trimmed = name.trim();

  const title = t(
    currentName
      ? I18nKey.SERVICE_ACCOUNTS$RENAME_TITLE
      : I18nKey.SERVICE_ACCOUNTS$CREATE,
  );

  return (
    <OrgModal
      title={title}
      ariaLabel={title}
      primaryButtonText={t(
        currentName ? I18nKey.BUTTON$RENAME : I18nKey.BUTTON$CREATE,
      )}
      secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
      onPrimaryClick={() => onSubmit(trimmed)}
      onClose={onClose}
      isLoading={isLoading}
      isPrimaryDisabled={!trimmed || trimmed === currentName}
      primaryButtonTestId="service-account-submit-button"
      secondaryButtonTestId="cancel-button"
    >
      <SettingsInput
        testId="service-account-name-input"
        label={t(I18nKey.SETTINGS$NAME)}
        type="text"
        value={name}
        onChange={setName}
        placeholder={t(I18nKey.SERVICE_ACCOUNTS$NAME_PLACEHOLDER)}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !isLoading &&
            trimmed &&
            trimmed !== currentName
          ) {
            onSubmit(trimmed);
          }
        }}
      />
    </OrgModal>
  );
}
