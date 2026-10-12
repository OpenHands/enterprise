import { useTranslation } from "react-i18next";
import { Typography } from "#/ui/typography";
import { I18nKey } from "#/i18n/declaration";
import InfoCircleIcon from "#/icons/info-circle.svg?react";

export type OrgWideSettingsBadgeVariant = "org-wide" | "managed-by-admin";

interface OrgWideSettingsBadgeProps {
  variant?: OrgWideSettingsBadgeVariant;
}

export function OrgWideSettingsBadge({
  variant = "org-wide",
}: OrgWideSettingsBadgeProps) {
  const { t } = useTranslation();

  const i18nKey =
    variant === "managed-by-admin"
      ? I18nKey.SETTINGS$ORG_MANAGED_BY_ADMIN_BADGE
      : I18nKey.SETTINGS$ORG_WIDE_SETTING_BADGE;

  return (
    <div
      data-testid="org-wide-settings-badge"
      className="flex w-full items-center justify-center gap-2 border-b border-[var(--oh-border)] bg-base-secondary px-4 py-1.5"
      role="status"
    >
      <InfoCircleIcon
        width={12}
        height={12}
        className="shrink-0 text-[var(--oh-muted)]"
      />
      <Typography.Text className="text-[11px] font-medium leading-5 text-[var(--oh-muted)]">
        {t(i18nKey)}
      </Typography.Text>
    </div>
  );
}
