import React from "react";
import { useTranslation } from "react-i18next";
import { ScimProviderOption } from "#/api/organization-service/scim.api";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { I18nKey } from "#/i18n/declaration";

interface CreateScimCredentialModalProps {
  /** The providers the caller may create a credential for; never empty. */
  providers: ScimProviderOption[];
  onSubmit: (name: string, authProviderId: number) => void;
  onClose: () => void;
  isLoading?: boolean;
}

/** Name and SSO provider of a new SCIM credential. */
export function CreateScimCredentialModal({
  providers,
  onSubmit,
  onClose,
  isLoading = false,
}: CreateScimCredentialModalProps) {
  const { t } = useTranslation();
  const [name, setName] = React.useState("");
  // With one choice there is nothing to select.
  const [providerId, setProviderId] = React.useState<number | null>(
    providers.length === 1 ? providers[0].id : null,
  );
  const trimmed = name.trim();
  const provider = providers.find((option) => option.id === providerId);
  const canSubmit = !!trimmed && !!provider && !isLoading;
  const submit = () => {
    if (canSubmit) onSubmit(trimmed, provider.id);
  };

  return (
    <OrgModal
      testId="create-scim-credential-modal"
      title={t(I18nKey.SCIM$CREATE)}
      ariaLabel={t(I18nKey.SCIM$CREATE)}
      description={t(I18nKey.SCIM$CREATE_DESCRIPTION)}
      primaryButtonText={t(I18nKey.BUTTON$CREATE)}
      secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
      onPrimaryClick={submit}
      onClose={onClose}
      isLoading={isLoading}
      isPrimaryDisabled={!canSubmit}
      primaryButtonTestId="scim-credential-submit-button"
      secondaryButtonTestId="cancel-button"
      className="w-[calc(100vw-2rem)] max-w-md"
    >
      <SettingsInput
        testId="scim-credential-name-input"
        label={t(I18nKey.SETTINGS$NAME)}
        type="text"
        value={name}
        onChange={setName}
        placeholder={t(I18nKey.SCIM$NAME_PLACEHOLDER)}
        className="w-full"
        onKeyDown={(event) => {
          if (event.key === "Enter") submit();
        }}
      />
      <SettingsDropdownInput
        testId="scim-credential-provider-input"
        name="scim-credential-provider"
        label={t(I18nKey.SCIM$PROVIDER)}
        placeholder={t(I18nKey.SCIM$PROVIDER_PLACEHOLDER)}
        wrapperClassName="w-full"
        items={providers.map((option) => ({
          key: String(option.id),
          label: option.bound
            ? option.title
            : t(I18nKey.SCIM$PROVIDER_NEW_BINDING, { title: option.title }),
        }))}
        selectedKey={providerId === null ? null : String(providerId)}
        onSelectionChange={(key) =>
          setProviderId(key === null ? null : Number(key))
        }
      />
      {provider && !provider.allow_match_by_email && (
        <p
          role="alert"
          data-testid="scim-no-match-by-email"
          className="text-xs leading-5 text-[var(--oh-status-warning,#f5a524)]"
        >
          {t(I18nKey.SCIM$NO_MATCH_BY_EMAIL)}
        </p>
      )}
    </OrgModal>
  );
}
