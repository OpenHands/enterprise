import { useState } from "react";
import { useTranslation } from "react-i18next";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { I18nKey } from "#/i18n/declaration";
import { useInstanceSettings } from "#/hooks/query/use-super-admin";
import { useUpdateInstanceSettings } from "#/hooks/mutation/use-super-admin-mutations";

/** The company name, saved when the field loses focus or on Enter. */
export function InstanceCompanyNameSetting() {
  const { t } = useTranslation();
  const { data: instanceSettings } = useInstanceSettings();
  const { mutate: updateInstanceSettings } = useUpdateInstanceSettings();
  const savedName = instanceSettings?.company_name ?? "";
  // Null until the admin edits, so the field follows the saved name.
  const [draft, setDraft] = useState<string | null>(null);

  const save = () => {
    if (draft === null) {
      return;
    }
    const name = draft.trim();
    if (name === savedName) {
      setDraft(null);
      return;
    }
    updateInstanceSettings(
      { company_name: name || null },
      { onSettled: () => setDraft(null) },
    );
  };

  return (
    <SettingsInput
      testId="instance-company-name"
      name="companyName"
      type="text"
      className="max-w-md"
      label={t(I18nKey.SA_NUX$COMPANY_NAME_LABEL)}
      hint={t(I18nKey.SUPER_ADMIN$INSTANCE_COMPANY_NAME_HINT)}
      placeholder={t(I18nKey.SA_NUX$COMPANY_NAME_PLACEHOLDER)}
      value={draft ?? savedName}
      onChange={setDraft}
      onBlur={save}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.currentTarget.blur();
        }
      }}
    />
  );
}
