import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AccountLink } from "#/api/native-auth-service/native-auth-service.api";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { SettingsInput } from "#/components/features/settings/settings-input";

export function AccountLinkModal({
  link,
  onClose,
}: {
  link: AccountLink;
  onClose: () => void;
}): React.JSX.Element {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const copyLink = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(
        link.invite_url || link.reset_url || "",
      );
      setCopied(true);
      setCopyFailed(false);
    } catch {
      setCopyFailed(true);
    }
  };
  return (
    <OrgModal
      title={t("NATIVE_AUTH$SHARE_LINK")}
      ariaLabel={t("NATIVE_AUTH$SHARE_LINK")}
      className="ph-no-capture ph-mask max-w-full"
      description={t("NATIVE_AUTH$COPY_ONCE_HELP")}
      primaryButtonText={t(
        copied ? "NATIVE_AUTH$COPIED" : "NATIVE_AUTH$COPY_LINK",
      )}
      secondaryButtonText={t("NATIVE_AUTH$CLOSE")}
      onPrimaryClick={copyLink}
      onClose={onClose}
    >
      <p className="text-sm">
        {t("NATIVE_AUTH$EXPIRES", {
          date: new Date(link.expires_at).toLocaleString(),
        })}
      </p>
      <SettingsInput
        className="w-full ph-no-capture"
        type="text"
        label={t("NATIVE_AUTH$PRIVATE_LINK")}
        readOnly
        value={link.invite_url || link.reset_url || ""}
        onFocus={(event: React.FocusEvent<HTMLInputElement>): void =>
          event.currentTarget.select()
        }
      />
      {copyFailed && <p role="status">{t("NATIVE_AUTH$COPY_MANUALLY")}</p>}
    </OrgModal>
  );
}
