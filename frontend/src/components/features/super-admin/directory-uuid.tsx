import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CopyToClipboardButton } from "#/components/shared/buttons/copy-to-clipboard-button";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";

export function DirectoryUuid({ uuid }: { uuid: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return undefined;
    const timeout = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timeout);
  }, [copied]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(uuid);
      setCopied(true);
    } catch {
      displayErrorToast(t(I18nKey.CHAT_INTERFACE$CHAT_MESSAGE_COPY_FAILED));
    }
  };

  return (
    <div
      className="flex min-w-0 items-center gap-1 text-xs text-[var(--oh-muted)]"
      onClick={(event) => event.stopPropagation()}
    >
      <span className="shrink-0">{t(I18nKey.SUPER_ADMIN$UUID)}</span>
      <span className="truncate font-mono select-text" title={uuid}>
        {uuid}
      </span>
      <CopyToClipboardButton
        isHidden={false}
        isDisabled={copied}
        mode={copied ? "copied" : "copy"}
        onClick={copy}
      />
    </div>
  );
}
