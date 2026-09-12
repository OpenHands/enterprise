import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { Text } from "#/ui/typography";

export interface WebhookStatusBadgeProps {
  webhookInstalled: boolean;
  installationResult?: { success: boolean; error: string | null } | null;
}

export function WebhookStatusBadge({
  webhookInstalled,
  installationResult,
}: WebhookStatusBadgeProps) {
  const { t } = useTranslation();

  if (installationResult) {
    if (installationResult.success) {
      return (
        <Text className="rounded bg-emerald-500/20 px-2 py-1 text-xs text-[var(--oh-status-success)]">
          {t(I18nKey.GITLAB$WEBHOOK_STATUS_INSTALLED)}
        </Text>
      );
    }
    return (
      <span title={installationResult.error || undefined}>
        <Text className="rounded bg-red-500/20 px-2 py-1 text-xs text-[var(--oh-status-error)]">
          {t(I18nKey.GITLAB$WEBHOOK_STATUS_FAILED)}
        </Text>
      </span>
    );
  }

  if (webhookInstalled) {
    return (
      <Text className="rounded bg-emerald-500/20 px-2 py-1 text-xs text-[var(--oh-status-success)]">
        {t(I18nKey.GITLAB$WEBHOOK_STATUS_INSTALLED)}
      </Text>
    );
  }

  return (
    <Text className="rounded bg-[var(--oh-interactive-hover)] px-2 py-1 text-xs text-[var(--oh-muted)]">
      {t(I18nKey.GITLAB$WEBHOOK_STATUS_NOT_INSTALLED)}
    </Text>
  );
}
