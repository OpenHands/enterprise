import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import { formControlMultilineFieldClassName } from "#/utils/form-control-classes";
import { BrandButton } from "#/components/features/settings/brand-button";
import { EyeIcon, EyeOffIcon } from "#/components/shared/icons/inline-icons";

interface MaskedSecretTextareaProps {
  testId: string;
  toggleTestId: string;
  rows: number;
  name?: string;
  required?: boolean;
  value?: string;
  onChange?: React.ChangeEventHandler<HTMLTextAreaElement>;
}

/**
 * A secret value field that is masked until revealed. It is a textarea, not
 * an `<input type="password">`, because single-line inputs strip newlines and
 * would corrupt multi-line secrets such as PEM keys.
 */
export function MaskedSecretTextarea({
  testId,
  toggleTestId,
  rows,
  name,
  required,
  value,
  onChange,
}: MaskedSecretTextareaProps) {
  const { t } = useTranslation();
  const [showValue, setShowValue] = React.useState(false);

  return (
    <div className="flex items-start gap-1.5">
      <textarea
        data-testid={testId}
        rows={rows}
        name={name}
        required={required}
        value={value}
        onChange={onChange}
        data-masked={!showValue}
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        className={cn(
          formControlMultilineFieldClassName,
          "resize-none",
          !showValue && "[-webkit-text-security:disc]",
        )}
      />
      <BrandButton
        testId={toggleTestId}
        type="button"
        variant="secondary"
        ariaLabel={t(
          showValue ? I18nKey.SECRETS$HIDE_VALUE : I18nKey.SECRETS$SHOW_VALUE,
        )}
        onClick={() => setShowValue((visible) => !visible)}
      >
        {showValue ? <EyeOffIcon /> : <EyeIcon />}
      </BrandButton>
    </div>
  );
}
