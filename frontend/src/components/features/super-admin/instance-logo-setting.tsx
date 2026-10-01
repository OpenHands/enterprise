import { useRef, useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus } from "lucide-react";
import { I18nKey } from "#/i18n/declaration";
import {
  readImageFileAsDataUrl,
  setInstanceLogo,
  useInstanceLogo,
} from "#/utils/org/instance-logo";

export function InstanceLogoSetting() {
  const { t } = useTranslation();
  const logo = useInstanceLogo();
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !file.type.startsWith("image/")) {
      return;
    }
    try {
      setInstanceLogo(await readImageFileAsDataUrl(file));
      setError(null);
    } catch {
      setError(t(I18nKey.SUPER_ADMIN$INSTANCE_LOGO_ERROR));
    }
  };

  return (
    <div className="flex items-start gap-4" data-testid="instance-logo-setting">
      <button
        type="button"
        className="flex size-20 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-[var(--oh-border)] bg-base-secondary text-[var(--oh-muted)] hover:bg-surface-raised"
        data-testid="instance-logo-upload"
        aria-label={t(I18nKey.SUPER_ADMIN$INSTANCE_LOGO)}
        onClick={() => inputRef.current?.click()}
      >
        {logo ? (
          <img src={logo} alt="" className="size-full object-cover" />
        ) : (
          <ImagePlus className="size-5" strokeWidth={1.75} aria-hidden />
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="sr-only"
        tabIndex={-1}
        data-testid="instance-logo-input"
        onChange={onFile}
      />
      <div className="flex min-w-0 flex-col gap-1 pt-1">
        <p className="text-sm text-white">
          {t(I18nKey.SUPER_ADMIN$INSTANCE_LOGO)}
        </p>
        <p className="text-xs text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$INSTANCE_LOGO_HINT)}
        </p>
        {logo && (
          <button
            type="button"
            className="mt-1 w-fit text-xs text-[var(--oh-muted)] hover:text-white"
            data-testid="instance-logo-remove"
            onClick={() => setInstanceLogo(null)}
          >
            {t(I18nKey.SUPER_ADMIN$INSTANCE_LOGO_REMOVE)}
          </button>
        )}
        {error && (
          <p className="text-xs text-red-400" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
