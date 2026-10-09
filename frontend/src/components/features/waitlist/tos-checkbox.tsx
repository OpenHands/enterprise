import { useTranslation } from "react-i18next";
import { TERMS_OF_SERVICE_URL } from "#/constants/legal-links";
import { I18nKey } from "#/i18n/declaration";

interface TOSCheckboxProps {
  onChange: () => void;
}

export function TOSCheckbox({ onChange }: TOSCheckboxProps) {
  const { t } = useTranslation();
  return (
    <label className="flex items-center gap-2">
      <input type="checkbox" onChange={onChange} />
      <span>
        {t(I18nKey.TOS$ACCEPT)}{" "}
        <a
          href={TERMS_OF_SERVICE_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2 text-blue-500 hover:text-blue-700"
        >
          {t(I18nKey.TOS$TERMS)}
        </a>
      </span>
    </label>
  );
}
