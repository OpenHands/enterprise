import React from "react";
import { redirect } from "react-router";
import { useTranslation } from "react-i18next";
import { LoaderCircle, Plus, UserPlus } from "lucide-react";
import { queryClient } from "#/query-client-config";
import { QUERY_KEYS, CONFIG_CACHE_OPTIONS } from "#/hooks/query/query-keys";
import OptionService from "#/api/option-service/option-service.api";
import { WebClientConfig } from "#/api/option-service/option.types";
import { adminService } from "#/api/admin-service/admin-service.api";
import { useSuperAdminStatus } from "#/hooks/query/use-super-admin-status";
import { useAllOrganizations } from "#/hooks/query/use-all-organizations";
import { AdminInviteMemberModal } from "#/components/features/admin/admin-invite-member-modal";
import { CreateOrganizationModal } from "#/components/features/org/create-organization-modal";
import { AdminOrgSummary } from "#/api/admin-service/admin-service.types";
import { BrandButton } from "#/components/features/settings/brand-button";
import { Typography } from "#/ui/typography";
import { I18nKey } from "#/i18n/declaration";

/**
 * Guard the super-admin dashboard route. The dashboard is only mounted on
 * self-hosted enterprise installs where it is enabled; on cloud/SaaS or OSS the
 * ``/api/admin/super-admin-status`` endpoint is absent (404) and the
 * super-admin check fails, so we redirect non-super-admins away.
 */
export const clientLoader = async () => {
  const config = await queryClient.fetchQuery<WebClientConfig>({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
    ...CONFIG_CACHE_OPTIONS,
  });

  const isEnterpriseSelfHosted =
    config?.app_mode === "saas" &&
    config?.feature_flags?.deployment_mode === "self_hosted";

  if (!isEnterpriseSelfHosted) {
    return redirect("/settings");
  }

  try {
    const status = await queryClient.fetchQuery({
      queryKey: ["admin", "super-admin-status"],
      queryFn: adminService.getSuperAdminStatus,
    });
    if (!status.is_super_admin) {
      return redirect("/settings");
    }
  } catch {
    // Endpoint absent (dashboard disabled) or request failed -> no access.
    return redirect("/settings");
  }

  return null;
};

export function meta() {
  return [{ title: "Super Admin | OpenHands" }];
}

function SuperAdminDashboard() {
  const { t } = useTranslation();
  const { data: status } = useSuperAdminStatus();
  const isSuperAdmin = status?.is_super_admin === true;
  const {
    data: orgsData,
    isLoading,
    error,
  } = useAllOrganizations(isSuperAdmin);

  const [createModalOpen, setCreateModalOpen] = React.useState(false);
  const [inviteTarget, setInviteTarget] =
    React.useState<AdminOrgSummary | null>(null);

  const organizations = orgsData?.organizations ?? [];

  return (
    <div
      data-testid="super-admin-dashboard-screen"
      className="flex flex-col gap-6 p-6 w-full"
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <Typography.H3>{t(I18nKey.SUPER_ADMIN$TITLE)}</Typography.H3>
          <Typography.Text className="text-tertiary-alt text-sm">
            {t(I18nKey.SUPER_ADMIN$SUBTITLE)}
          </Typography.Text>
        </div>
        <BrandButton
          testId="super-admin-create-org-button"
          type="button"
          variant="primary"
          onClick={() => setCreateModalOpen(true)}
          startContent={<Plus size={16} />}
        >
          {t(I18nKey.ORG$CREATE_ORGANIZATION)}
        </BrandButton>
      </div>

      {isLoading && (
        <div
          data-testid="super-admin-orgs-loading"
          className="flex items-center gap-2 text-tertiary-alt"
        >
          <LoaderCircle className="animate-spin" size={18} />
          {t(I18nKey.HOME$LOADING)}
        </div>
      )}

      {error && (
        <Typography.Text
          testId="super-admin-orgs-error"
          className="text-red-400 text-sm"
        >
          {t(I18nKey.SUPER_ADMIN$LOAD_ORGS_ERROR)}
        </Typography.Text>
      )}

      {!isLoading && !error && organizations.length === 0 && (
        <Typography.Text
          testId="super-admin-orgs-empty"
          className="text-tertiary-alt text-sm"
        >
          {t(I18nKey.SUPER_ADMIN$NO_ORGS)}
        </Typography.Text>
      )}

      {organizations.length > 0 && (
        <ul
          data-testid="super-admin-orgs-list"
          className="flex flex-col divide-y divide-[#242424] border border-[#242424] rounded-lg"
        >
          {organizations.map((org) => (
            <li
              key={org.id}
              data-testid="super-admin-org-row"
              className="flex items-center justify-between gap-4 p-4"
            >
              <div className="flex flex-col min-w-0">
                <span className="font-medium truncate">{org.name}</span>
                {org.contact_email && (
                  <span className="text-xs text-tertiary-alt truncate">
                    {org.contact_email}
                  </span>
                )}
              </div>
              <BrandButton
                testId="super-admin-invite-button"
                type="button"
                variant="secondary"
                onClick={() => setInviteTarget(org)}
                startContent={<UserPlus size={16} />}
              >
                {t(I18nKey.ORG$INVITE_ORG_MEMBERS)}
              </BrandButton>
            </li>
          ))}
        </ul>
      )}

      {createModalOpen && (
        <CreateOrganizationModal onClose={() => setCreateModalOpen(false)} />
      )}

      {inviteTarget && (
        <AdminInviteMemberModal
          orgId={inviteTarget.id}
          orgName={inviteTarget.name}
          onClose={() => setInviteTarget(null)}
        />
      )}
    </div>
  );
}

export default SuperAdminDashboard;
