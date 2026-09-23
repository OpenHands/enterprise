import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { CreateOrganizationModal } from "#/components/features/org/create-organization-modal";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useConfig } from "#/hooks/query/use-config";
import { useMe } from "#/hooks/query/use-me";
import {
  useDeleteSuperAdminOrganization,
  useGrantSuperAdmin,
  useProvisionSuperAdminUser,
  useRemoveSuperAdminUser,
  useRevokeSuperAdmin,
  useUpdateSuperAdminOrganizationStatus,
  useUpdateSuperAdminUserStatus,
} from "#/hooks/mutation/use-super-admin-mutations";
import {
  useSuperAdminOrganizations,
  useSuperAdmins,
  useSuperAdminUsers,
} from "#/hooks/query/use-super-admin";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import {
  SUPER_ADMIN_NAV_ITEMS,
  SUPER_ADMIN_PATHS,
} from "#/constants/super-admin-nav";
import {
  BrandButton,
  SettingsInput,
  SuperAdminPageHeader,
  SuperAdminRowMenu,
  SuperAdminSearchField,
  SuperAdminStatusLabel,
  SuperAdminTable,
  SuperAdminUserMemberships,
  USER_TABLE_CELL_CLASS_NAME,
} from "./super-admin-chrome";
import { SuperAdminDashboard } from "./super-admin-dashboard";
import { SuperAdminSetupGuide } from "./super-admin-setup-guide";
import {
  setSuperAdminSetupVisible,
  useSuperAdminSetup,
} from "./super-admin-setup";
import type {
  SuperAdminAdminRow,
  SuperAdminMembership,
  SuperAdminOrgRole,
  SuperAdminOrgRow,
  SuperAdminUserRow,
} from "./super-admin-mock";
import { useSuperAdminOpenOrg } from "./use-super-admin-open-org";

function navCopy(path: string) {
  return (
    SUPER_ADMIN_NAV_ITEMS.find((item) => item.to === path) ??
    SUPER_ADMIN_NAV_ITEMS[0]
  );
}

function toOrgRole(role: string): SuperAdminOrgRole {
  if (role === "owner" || role === "admin") {
    return role;
  }
  return "member";
}

function deriveUserStatus(
  memberships: { status: string | null }[],
): SuperAdminUserRow["status"] {
  if (memberships.length === 0) {
    return "active";
  }
  if (memberships.every((m) => m.status === "inactive")) {
    return "inactive";
  }
  if (memberships.some((m) => m.status === "invited")) {
    return "invited";
  }
  return "active";
}

export function SuperAdminOverview() {
  return <SuperAdminDashboard />;
}

export function SuperAdminSetup() {
  return <SuperAdminSetupGuide />;
}

export function SuperAdminOrganizations() {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const openOrg = useSuperAdminOpenOrg();
  const copy = navCopy(SUPER_ADMIN_PATHS.organizations);
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const { data, isLoading, isError } = useSuperAdminOrganizations();
  const deleteOrg = useDeleteSuperAdminOrganization();
  const updateOrgStatus = useUpdateSuperAdminOrganizationStatus();

  const orgs: SuperAdminOrgRow[] = useMemo(
    () =>
      (data ?? []).map((org) => ({
        id: org.id,
        name: org.name,
        members: org.member_count,
        status: org.status === "suspended" ? "suspended" : "active",
        contactEmail: org.contact_email ?? "",
        isPersonal: org.is_personal,
      })),
    [data],
  );

  const rows = useMemo(
    () =>
      orgs.filter((org) =>
        `${org.name} ${org.contactEmail}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
      ),
    [query, orgs],
  );

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="super-admin-organizations"
    >
      <SuperAdminPageHeader
        title={t(copy.text)}
        subtitle={t(copy.subtitle)}
        action={
          <BrandButton
            type="button"
            variant="primary"
            startContent={<Plus className="h-4 w-4" />}
            onClick={() => setCreateOpen(true)}
          >
            {t(I18nKey.ORG$CREATE_ORGANIZATION)}
          </BrandButton>
        }
      />
      <SuperAdminSearchField
        testId="super-admin-org-search"
        value={query}
        placeholder={t(I18nKey.SUPER_ADMIN$SEARCH_ORGS)}
        onChange={setQuery}
      />
      {isLoading ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$LOADING)}
        </p>
      ) : null}
      {isError ? (
        <p className="text-sm text-red-400">
          {t(I18nKey.SUPER_ADMIN$LOAD_ERROR)}
        </p>
      ) : null}
      <SuperAdminTable<SuperAdminOrgRow>
        testId="super-admin-orgs-table"
        rows={rows}
        getRowKey={(row) => row.id}
        empty={t(I18nKey.SUPER_ADMIN$EMPTY_ORGS)}
        columns={[
          {
            key: "name",
            header: t(I18nKey.ORG$ORGANIZATION_NAME),
            className: "w-[28%]",
            render: (row) => (
              <button
                type="button"
                data-testid={`super-admin-org-open-${row.id}`}
                className="block max-w-full truncate text-left hover:underline"
                onClick={() => openOrg(row.id, row.name)}
              >
                {row.name}
              </button>
            ),
          },
          {
            key: "members",
            header: t(I18nKey.SUPER_ADMIN$COL_MEMBERS),
            className: "w-[12%]",
            render: (row) => row.members,
          },
          {
            key: "email",
            header: t(I18nKey.ORG$CONTACT_EMAIL),
            className: "w-[36%]",
            render: (row) => (
              <span className="block truncate text-[var(--oh-muted)]">
                {row.contactEmail || "—"}
              </span>
            ),
          },
          {
            key: "status",
            header: t(I18nKey.SUPER_ADMIN$COL_STATUS),
            className: "w-[14%]",
            render: (row) => <SuperAdminStatusLabel status={row.status} />,
          },
          {
            key: "actions",
            header: "",
            className: "w-12 min-w-12 text-right",
            render: (row) => (
              <SuperAdminRowMenu
                testId={`super-admin-org-actions-${row.id}`}
                ariaLabel={t(I18nKey.SUPER_ADMIN$ROW_ACTIONS)}
                items={[
                  {
                    label: t(I18nKey.SUPER_ADMIN$VIEW_ORG),
                    testId: `super-admin-org-view-${row.id}`,
                    onSelect: () => openOrg(row.id, row.name),
                  },
                  row.status === "active"
                    ? {
                        label: t(I18nKey.SUPER_ADMIN$SUSPEND),
                        testId: `super-admin-org-suspend-${row.id}`,
                        onSelect: () =>
                          updateOrgStatus.mutate({
                            orgId: row.id,
                            status: "suspended",
                          }),
                      }
                    : {
                        label: t(I18nKey.SUPER_ADMIN$RESUME),
                        testId: `super-admin-org-resume-${row.id}`,
                        onSelect: () =>
                          updateOrgStatus.mutate({
                            orgId: row.id,
                            status: "active",
                          }),
                      },
                  {
                    label: t(I18nKey.SUPER_ADMIN$REMOVE),
                    testId: `super-admin-org-remove-${row.id}`,
                    destructive: true,
                    onSelect: () => deleteOrg.mutate({ orgId: row.id }),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      {createOpen && (
        <CreateOrganizationModal
          contactEmail={me?.email}
          onClose={() => setCreateOpen(false)}
        />
      )}
    </div>
  );
}

export function SuperAdminUsers() {
  const { t } = useTranslation();
  const openOrg = useSuperAdminOpenOrg();
  const copy = navCopy(SUPER_ADMIN_PATHS.users);
  const [query, setQuery] = useState("");
  const [provisionOpen, setProvisionOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [orgId, setOrgId] = useState("");
  const { data, isLoading, isError } = useSuperAdminUsers();
  const { data: orgs } = useSuperAdminOrganizations();
  const provision = useProvisionSuperAdminUser();
  const updateUserStatus = useUpdateSuperAdminUserStatus();
  const removeUser = useRemoveSuperAdminUser();

  const users: SuperAdminUserRow[] = useMemo(
    () =>
      (data ?? []).map((user) => {
        const memberships: SuperAdminMembership[] = user.memberships.map(
          (membership) => ({
            orgId: membership.org_id,
            orgName: membership.org_name,
            role: toOrgRole(membership.role),
          }),
        );
        return {
          id: user.user_id,
          name: user.name || user.email || user.user_id,
          email: user.email ?? "",
          memberships,
          status:
            user.status === "inactive"
              ? "inactive"
              : deriveUserStatus(user.memberships),
        };
      }),
    [data],
  );

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return users.filter((user) => {
      const membershipText = user.memberships
        .map((membership) => `${membership.orgName} ${membership.role}`)
        .join(" ");
      return `${user.name} ${user.email} ${membershipText}`
        .toLowerCase()
        .includes(needle);
    });
  }, [query, users]);

  const teamOrgs = useMemo(
    () => (orgs ?? []).filter((org) => !org.is_personal),
    [orgs],
  );

  const handleProvision = () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !orgId) {
      return;
    }
    provision.mutate(
      { orgId, payload: { email: trimmedEmail, role: "member" } },
      {
        onSuccess: () => {
          setEmail("");
          setOrgId("");
          setProvisionOpen(false);
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-4" data-testid="super-admin-users">
      <SuperAdminPageHeader
        title={t(copy.text)}
        subtitle={t(copy.subtitle)}
        action={
          <BrandButton
            type="button"
            variant="primary"
            startContent={<Plus className="h-4 w-4" />}
            onClick={() => setProvisionOpen(true)}
          >
            {t(I18nKey.SUPER_ADMIN$PROVISION_USER)}
          </BrandButton>
        }
      />
      <SuperAdminSearchField
        testId="super-admin-user-search"
        value={query}
        placeholder={t(I18nKey.SUPER_ADMIN$SEARCH_USERS)}
        onChange={setQuery}
      />
      {isLoading ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$LOADING)}
        </p>
      ) : null}
      {isError ? (
        <p className="text-sm text-red-400">
          {t(I18nKey.SUPER_ADMIN$LOAD_ERROR)}
        </p>
      ) : null}
      <SuperAdminTable<SuperAdminUserRow>
        testId="super-admin-users-table"
        rows={rows}
        getRowKey={(row) => row.id}
        empty={t(I18nKey.SUPER_ADMIN$EMPTY_USERS)}
        columns={[
          {
            key: "name",
            header: t(I18nKey.SUPER_ADMIN$COL_NAME),
            className: cn("w-[20%]", USER_TABLE_CELL_CLASS_NAME),
            render: (row) => <span className="block truncate">{row.name}</span>,
          },
          {
            key: "email",
            header: t(I18nKey.ORG$CONTACT_EMAIL),
            className: cn("w-[28%]", USER_TABLE_CELL_CLASS_NAME),
            render: (row) => (
              <span className="block truncate text-[var(--oh-muted)]">
                {row.email}
              </span>
            ),
          },
          {
            key: "org",
            header: t(I18nKey.SUPER_ADMIN$COL_ORG),
            className: cn("w-[24%]", USER_TABLE_CELL_CLASS_NAME),
            render: (row) => (
              <SuperAdminUserMemberships
                memberships={row.memberships}
                field="orgName"
                onOrgClick={openOrg}
              />
            ),
          },
          {
            key: "role",
            header: t(I18nKey.SUPER_ADMIN$COL_ROLE),
            className: cn("w-[14%]", USER_TABLE_CELL_CLASS_NAME),
            render: (row) => (
              <SuperAdminUserMemberships
                memberships={row.memberships}
                field="role"
              />
            ),
          },
          {
            key: "status",
            header: t(I18nKey.SUPER_ADMIN$COL_STATUS),
            className: cn("w-[12%]", USER_TABLE_CELL_CLASS_NAME),
            render: (row) => <SuperAdminStatusLabel status={row.status} />,
          },
          {
            key: "actions",
            header: "",
            className: cn(
              "w-12 min-w-12 text-right",
              USER_TABLE_CELL_CLASS_NAME,
            ),
            render: (row) => (
              <SuperAdminRowMenu
                testId={`super-admin-user-actions-${row.id}`}
                ariaLabel={t(I18nKey.SUPER_ADMIN$ROW_ACTIONS)}
                items={[
                  row.status === "inactive"
                    ? {
                        label: t(I18nKey.SUPER_ADMIN$RESUME),
                        testId: `super-admin-user-resume-${row.id}`,
                        onSelect: () =>
                          updateUserStatus.mutate({
                            userId: row.id,
                            status: "active",
                          }),
                      }
                    : {
                        label: t(I18nKey.SUPER_ADMIN$SUSPEND),
                        testId: `super-admin-user-suspend-${row.id}`,
                        onSelect: () =>
                          updateUserStatus.mutate({
                            userId: row.id,
                            status: "inactive",
                          }),
                      },
                  {
                    label: t(I18nKey.SUPER_ADMIN$REMOVE),
                    testId: `super-admin-user-remove-${row.id}`,
                    destructive: true,
                    onSelect: () => removeUser.mutate({ userId: row.id }),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      {provisionOpen && (
        <OrgModal
          testId="super-admin-provision-form"
          title={t(I18nKey.SUPER_ADMIN$PROVISION_USER)}
          description={t(I18nKey.SUPER_ADMIN$PROVISION_USER_DESCRIPTION)}
          primaryButtonText={t(I18nKey.SUPER_ADMIN$PROVISION_USER)}
          onPrimaryClick={handleProvision}
          onClose={() => setProvisionOpen(false)}
        >
          <div className="flex w-full flex-col gap-3">
            <SettingsInput
              type="email"
              label={t(I18nKey.ORG$CONTACT_EMAIL)}
              value={email}
              placeholder={t(I18nKey.ORG$CONTACT_EMAIL_PLACEHOLDER)}
              onChange={setEmail}
            />
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="text-[var(--oh-muted)]">
                {t(I18nKey.SUPER_ADMIN$PROVISION_ORG)}
              </span>
              <select
                data-testid="super-admin-provision-org"
                className="rounded-lg border border-[var(--oh-border)] bg-[var(--oh-surface)] px-3 py-2 text-foreground"
                value={orgId}
                onChange={(event) => setOrgId(event.target.value)}
              >
                <option value="">
                  {t(I18nKey.SUPER_ADMIN$PROVISION_ORG_PLACEHOLDER)}
                </option>
                {teamOrgs.map((org) => (
                  <option key={org.id} value={org.id}>
                    {org.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </OrgModal>
      )}
    </div>
  );
}

export function SuperAdminAdmins() {
  const { t } = useTranslation();
  const copy = navCopy(SUPER_ADMIN_PATHS.admins);
  const [grantOpen, setGrantOpen] = useState(false);
  const [email, setEmail] = useState("");
  const { data, isLoading, isError } = useSuperAdmins();
  const grant = useGrantSuperAdmin();
  const revoke = useRevokeSuperAdmin();

  const admins: SuperAdminAdminRow[] = useMemo(
    () =>
      (data ?? []).map((admin) => ({
        id: admin.user_id,
        name: admin.email?.split("@")[0] || admin.user_id,
        email: admin.email ?? "",
      })),
    [data],
  );

  const handleGrant = () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      return;
    }
    grant.mutate(
      { email: trimmedEmail },
      {
        onSuccess: () => {
          setEmail("");
          setGrantOpen(false);
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-4" data-testid="super-admin-admins">
      <SuperAdminPageHeader
        title={t(copy.text)}
        subtitle={t(copy.subtitle)}
        action={
          <BrandButton
            type="button"
            variant="primary"
            startContent={<Plus className="h-4 w-4" />}
            onClick={() => setGrantOpen(true)}
          >
            {t(I18nKey.SUPER_ADMIN$GRANT_ADMIN)}
          </BrandButton>
        }
      />
      <p className="text-sm text-[var(--oh-muted)]">
        {t(I18nKey.SUPER_ADMIN$ADMINS_HINT)}
      </p>
      {isLoading ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$LOADING)}
        </p>
      ) : null}
      {isError ? (
        <p className="text-sm text-red-400">
          {t(I18nKey.SUPER_ADMIN$LOAD_ERROR)}
        </p>
      ) : null}
      <SuperAdminTable<SuperAdminAdminRow>
        testId="super-admin-admins-table"
        rows={admins}
        getRowKey={(row) => row.id}
        empty={t(I18nKey.SUPER_ADMIN$EMPTY_ADMINS)}
        columns={[
          {
            key: "name",
            header: t(I18nKey.SUPER_ADMIN$COL_NAME),
            render: (row) => row.name,
          },
          {
            key: "email",
            header: t(I18nKey.ORG$CONTACT_EMAIL),
            render: (row) => row.email,
          },
          {
            key: "actions",
            header: "",
            className: "w-12 min-w-12 text-right",
            render: (row) => (
              <SuperAdminRowMenu
                testId={`super-admin-admin-actions-${row.id}`}
                ariaLabel={t(I18nKey.SUPER_ADMIN$ROW_ACTIONS)}
                items={[
                  {
                    label: t(I18nKey.SUPER_ADMIN$REVOKE),
                    testId: `super-admin-admin-revoke-${row.id}`,
                    destructive: true,
                    onSelect: () => revoke.mutate({ userId: row.id }),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      {grantOpen && (
        <OrgModal
          testId="super-admin-grant-form"
          title={t(I18nKey.SUPER_ADMIN$GRANT_ADMIN)}
          description={t(I18nKey.SUPER_ADMIN$GRANT_ADMIN_DESCRIPTION)}
          primaryButtonText={t(I18nKey.SUPER_ADMIN$GRANT_ADMIN)}
          onPrimaryClick={handleGrant}
          onClose={() => setGrantOpen(false)}
        >
          <SettingsInput
            type="email"
            label={t(I18nKey.ORG$CONTACT_EMAIL)}
            value={email}
            placeholder={t(I18nKey.ORG$CONTACT_EMAIL_PLACEHOLDER)}
            onChange={setEmail}
          />
        </OrgModal>
      )}
    </div>
  );
}

export function SuperAdminInstance() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const emailEnabled = Boolean(config?.email_enabled);
  const { visible: setupVisible } = useSuperAdminSetup();

  return (
    <div className="flex flex-col gap-6" data-testid="super-admin-instance">
      <p className="text-sm text-[var(--oh-muted)]">
        {t(I18nKey.SUPER_ADMIN$INSTANCE_HINT)}
      </p>
      <div className="flex flex-col gap-1">
        <SettingsSwitch
          isToggled={emailEnabled}
          isDisabled
          onToggle={() => undefined}
        >
          {t(I18nKey.SUPER_ADMIN$INSTANCE_EMAIL)}
        </SettingsSwitch>
        <p className="pl-0 text-xs text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$INSTANCE_EMAIL_HINT)}
        </p>
      </div>
      <div className="flex flex-col gap-1">
        <SettingsSwitch isToggled={false} isDisabled onToggle={() => undefined}>
          {t(I18nKey.SUPER_ADMIN$INSTANCE_AUTO_ORG)}
        </SettingsSwitch>
        <p className="text-xs text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$INSTANCE_AUTO_ORG_HINT)}
        </p>
      </div>
      <SettingsSwitch
        testId="super-admin-instance-setup-guide"
        isToggled={setupVisible}
        onToggle={setSuperAdminSetupVisible}
      >
        {t(I18nKey.SUPER_ADMIN$INSTANCE_SETUP_GUIDE)}
      </SettingsSwitch>
    </div>
  );
}
