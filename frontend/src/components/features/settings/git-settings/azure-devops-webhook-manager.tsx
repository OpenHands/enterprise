import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { HorizontalScrollFade } from "#/components/shared/horizontal-scroll-fade";
import type { AzureDevOpsWebhookStatus } from "#/api/integration-service/integration-service.types";
import { useAzureDevOpsOrganizations } from "#/hooks/query/use-azure-devops-organizations";
import { useAzureDevOpsResources } from "#/hooks/query/use-azure-devops-resources-list";
import { useReinstallAzureDevOpsWebhook } from "#/hooks/mutation/use-reinstall-azure-devops-webhook";
import { useUninstallAzureDevOpsWebhook } from "#/hooks/mutation/use-uninstall-azure-devops-webhook";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import { Typography } from "#/ui/typography";
import {
  settingsListContainerClassName,
  settingsListScrollFadeFromClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
} from "#/utils/settings-list-classes";

interface AzureDevOpsWebhookManagerProps {
  className?: string;
}

function StatusBadge({ status }: { status: AzureDevOpsWebhookStatus }) {
  const { t } = useTranslation();

  if (!status.webhook_secret_set) {
    return (
      <Typography.Text className="px-2 py-1 text-xs rounded bg-red-500/20 text-red-400">
        {t(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_MISSING_SECRET)}
      </Typography.Text>
    );
  }

  // Azure hides the hooks from members who can't view them, so they see
  // OpenHands' own install record instead.
  const installed = status.can_view_webhooks
    ? status.webhook_installed
    : status.install_recorded;

  if (installed) {
    return (
      <Typography.Text className="px-2 py-1 text-xs rounded bg-green-500/20 text-green-400">
        {t(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_INSTALLED)}
      </Typography.Text>
    );
  }

  if (!status.can_view_webhooks) {
    return (
      <Typography.Text className="px-2 py-1 text-xs rounded bg-gray-500/20 text-gray-400">
        {t(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_NOT_SET_UP)}
      </Typography.Text>
    );
  }

  if (status.pr_webhook_installed || status.work_item_webhook_installed) {
    return (
      <Typography.Text className="px-2 py-1 text-xs rounded bg-yellow-500/20 text-yellow-300">
        {t(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_PARTIAL)}
      </Typography.Text>
    );
  }

  return (
    <Typography.Text className="px-2 py-1 text-xs rounded bg-gray-500/20 text-gray-400">
      {t(I18nKey.AZURE_DEVOPS$WEBHOOK_STATUS_NOT_INSTALLED)}
    </Typography.Text>
  );
}

function InstallDetails({ status }: { status: AzureDevOpsWebhookStatus }) {
  const { t, i18n } = useTranslation();
  const formatDate = (value: string) =>
    new Date(value).toLocaleDateString(i18n.language, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  const parts = [
    status.installed_at &&
      t(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALLED_ON, {
        date: formatDate(status.installed_at),
      }),
    status.last_verified_at &&
      t(I18nKey.AZURE_DEVOPS$WEBHOOK_LAST_CONFIRMED, {
        date: formatDate(status.last_verified_at),
      }),
  ].filter(Boolean);

  if (!status.install_recorded || parts.length === 0) {
    return null;
  }
  return (
    <Typography.Text
      className="block mt-2 text-xs text-gray-400"
      testId="azure-devops-webhook-install-details"
    >
      {parts.join(" · ")}
    </Typography.Text>
  );
}

function OrganizationRow({ organization }: { organization: string }) {
  const { t } = useTranslation();
  const {
    data: status,
    isLoading,
    isError,
  } = useAzureDevOpsResources(true, organization);
  const reinstallMutation = useReinstallAzureDevOpsWebhook(organization);
  const uninstallMutation = useUninstallAzureDevOpsWebhook(organization);
  const isInstalling = reinstallMutation.isPending;
  const isUninstalling = uninstallMutation.isPending;

  if (isLoading || isError || !status) {
    return (
      <tr>
        <td className="px-4 py-3 text-white">{organization}</td>
        <td
          colSpan={2}
          className={cn(
            "px-4 py-3",
            isError ? "text-red-400" : "text-gray-400",
          )}
        >
          {t(
            isError
              ? I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_ERROR
              : I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_LOADING,
          )}
        </td>
      </tr>
    );
  }

  const anyMutationPending = isInstalling || isUninstalling;
  const canManage = status.can_manage_webhooks;
  const installDisabled = anyMutationPending || !status.webhook_secret_set;
  let installLabel = t(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALL);
  if (isInstalling) {
    installLabel = t(I18nKey.AZURE_DEVOPS$WEBHOOK_INSTALLING);
  } else if (status.webhook_installed) {
    installLabel = t(I18nKey.AZURE_DEVOPS$WEBHOOK_REINSTALL);
  }

  return (
    <tr className="hover:bg-neutral-800/50 transition-colors align-top">
      <td className="px-4 py-3">
        <Typography.Text className="text-sm font-medium text-white">
          {organization}
        </Typography.Text>
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={status} />
        <InstallDetails status={status} />
      </td>
      <td className="px-4 py-3">
        {canManage ? (
          <div className="flex gap-2">
            <BrandButton
              type="button"
              variant="primary"
              onClick={() => reinstallMutation.mutate()}
              isDisabled={installDisabled}
              className="cursor-pointer"
              testId="azure-devops-install-webhook"
            >
              {installLabel}
            </BrandButton>
            {status.webhook_installed && (
              <BrandButton
                type="button"
                variant="secondary"
                onClick={() => uninstallMutation.mutate()}
                isDisabled={anyMutationPending}
                className="cursor-pointer"
                testId="azure-devops-uninstall-webhook"
              >
                {isUninstalling
                  ? t(I18nKey.AZURE_DEVOPS$WEBHOOK_UNINSTALLING)
                  : t(I18nKey.AZURE_DEVOPS$WEBHOOK_UNINSTALL)}
              </BrandButton>
            )}
          </div>
        ) : (
          <Typography.Text
            className="block text-xs text-gray-400 max-w-xs"
            testId="azure-devops-webhook-permission-note"
          >
            {t(I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGED_BY_ADMINS)}
          </Typography.Text>
        )}
      </td>
    </tr>
  );
}

function OrganizationsTable({ organizations }: { organizations: string[] }) {
  const { t } = useTranslation();
  return organizations.length === 0 ? (
    <Typography.Text className="text-sm text-gray-400">
      {t(I18nKey.AZURE_DEVOPS$NO_ORGANIZATIONS)}
    </Typography.Text>
  ) : (
    <div
      className={cn(
        settingsListContainerClassName,
        settingsListScrollFadeFromClassName,
      )}
    >
      <HorizontalScrollFade>
        <table className="w-full">
          <thead className={settingsListTableHeadClassName}>
            <tr>
              <th className={settingsListTableHeaderCellClassName}>
                {t(I18nKey.AZURE_DEVOPS$WEBHOOK_COLUMN_ORGANIZATION)}
              </th>
              <th className={settingsListTableHeaderCellClassName}>
                {t(I18nKey.AZURE_DEVOPS$WEBHOOK_COLUMN_STATUS)}
              </th>
              <th className={settingsListTableHeaderCellClassName}>
                {t(I18nKey.AZURE_DEVOPS$WEBHOOK_COLUMN_ACTION)}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-700">
            {organizations.map((organization) => (
              <OrganizationRow key={organization} organization={organization} />
            ))}
          </tbody>
        </table>
      </HorizontalScrollFade>
    </div>
  );
}

export function AzureDevOpsWebhookManager({
  className,
}: AzureDevOpsWebhookManagerProps) {
  const { t } = useTranslation();
  const { data, isLoading, isError } = useAzureDevOpsOrganizations();

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      <Typography.H3 className="text-lg font-medium text-white">
        {t(I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_TITLE)}
      </Typography.H3>
      <Typography.Text className="text-sm text-gray-400">
        {t(I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_DESCRIPTION)}
      </Typography.Text>
      {isLoading || isError || !data ? (
        <Typography.Text
          className={cn("text-sm", isError ? "text-red-400" : "text-gray-400")}
        >
          {t(
            isError
              ? I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_ERROR
              : I18nKey.AZURE_DEVOPS$WEBHOOK_MANAGER_LOADING,
          )}
        </Typography.Text>
      ) : (
        <OrganizationsTable organizations={data.organizations} />
      )}
    </div>
  );
}
