import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { CreateOrganizationModal } from "#/components/features/org/create-organization-modal";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useMe } from "#/hooks/query/use-me";
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
import {
  SUPER_ADMIN_ADMINS,
  SUPER_ADMIN_ORGS,
  SUPER_ADMIN_USERS,
  type SuperAdminAdminRow,
  type SuperAdminOrgRow,
  type SuperAdminOrgStatus,
  type SuperAdminUserRow,
} from "./super-admin-mock";
import { useSuperAdminOpenOrg } from "./use-super-admin-open-org";

function navCopy(path: string) {
  return (
    SUPER_ADMIN_NAV_ITEMS.find((item) => item.to === path) ??
    SUPER_ADMIN_NAV_ITEMS[0]
  );
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
  const [orgs, setOrgs] = useState(SUPER_ADMIN_ORGS);

  const rows = useMemo(
    () =>
      orgs.filter((org) =>
        `${org.name} ${org.contactEmail}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
      ),
    [query, orgs],
  );

  const setOrgStatus = (id: string, status: SuperAdminOrgStatus) => {
    setOrgs((current) =>
      current.map((org) => (org.id === id ? { ...org, status } : org)),
    );
  };

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
      <SuperAdminTable<SuperAdminOrgRow>
        testId="super-admin-orgs-table"
        rows={rows}
        getRowKey={(row) => row.id}
        empty={t(I18nKey.SUPER_ADMIN$EMPTY_ORGS)}
        columns={[
          {
            key: "name",
            header: t(I18nKey.ORG$ORGANIZATION_NAME),
            className: "w-[24%]",
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
            className: "w-[32%]",
            render: (row) => (
              <span className="block truncate text-[var(--oh-muted)]">
                {row.contactEmail}
              </span>
            ),
          },
          {
            key: "status",
            header: t(I18nKey.SUPER_ADMIN$COL_STATUS),
            className: "w-[16%]",
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
                        onSelect: () => setOrgStatus(row.id, "suspended"),
                      }
                    : {
                        label: t(I18nKey.SUPER_ADMIN$RESUME),
                        testId: `super-admin-org-resume-${row.id}`,
                        onSelect: () => setOrgStatus(row.id, "active"),
                      },
                  {
                    label: t(I18nKey.SUPER_ADMIN$REMOVE),
                    testId: `super-admin-org-remove-${row.id}`,
                    destructive: true,
                    onSelect: () => setOrgStatus(row.id, "removed"),
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
  const [users, setUsers] = useState(SUPER_ADMIN_USERS);
  const [provisionOpen, setProvisionOpen] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");

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

  const setUserStatus = (id: string, status: SuperAdminUserRow["status"]) => {
    setUsers((current) =>
      current.map((user) => (user.id === id ? { ...user, status } : user)),
    );
  };

  const handleProvision = () => {
    const trimmedName = name.trim();
    const trimmedEmail = email.trim();
    if (!trimmedName || !trimmedEmail) {
      return;
    }
    setUsers((current) => [
      {
        id: `local-${current.length + 1}`,
        name: trimmedName,
        email: trimmedEmail,
        memberships: [{ orgId: "2", orgName: "Acme Corp", role: "member" }],
        status: "invited",
      },
      ...current,
    ]);
    setName("");
    setEmail("");
    setProvisionOpen(false);
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
            className: cn("w-[24%]", USER_TABLE_CELL_CLASS_NAME),
            render: (row) => (
              <span className="block truncate text-[var(--oh-muted)]">
                {row.email}
              </span>
            ),
          },
          {
            key: "org",
            header: t(I18nKey.SUPER_ADMIN$COL_ORG),
            className: cn("w-[22%]", USER_TABLE_CELL_CLASS_NAME),
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
                  row.status === "active" || row.status === "invited"
                    ? {
                        label: t(I18nKey.SUPER_ADMIN$SUSPEND),
                        testId: `super-admin-user-suspend-${row.id}`,
                        onSelect: () => setUserStatus(row.id, "suspended"),
                      }
                    : {
                        label: t(I18nKey.SUPER_ADMIN$RESUME),
                        testId: `super-admin-user-resume-${row.id}`,
                        onSelect: () => setUserStatus(row.id, "active"),
                      },
                  {
                    label: t(I18nKey.SUPER_ADMIN$REMOVE),
                    testId: `super-admin-user-remove-${row.id}`,
                    destructive: true,
                    onSelect: () => setUserStatus(row.id, "removed"),
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
              type="text"
              label={t(I18nKey.SUPER_ADMIN$COL_NAME)}
              value={name}
              placeholder={t(I18nKey.ORG$CONTACT_NAME_PLACEHOLDER)}
              onChange={setName}
            />
            <SettingsInput
              type="email"
              label={t(I18nKey.ORG$CONTACT_EMAIL)}
              value={email}
              placeholder={t(I18nKey.ORG$CONTACT_EMAIL_PLACEHOLDER)}
              onChange={setEmail}
            />
          </div>
        </OrgModal>
      )}
    </div>
  );
}

export function SuperAdminAdmins() {
  const { t } = useTranslation();
  const copy = navCopy(SUPER_ADMIN_PATHS.admins);
  const [admins, setAdmins] = useState(SUPER_ADMIN_ADMINS);
  const [grantOpen, setGrantOpen] = useState(false);
  const [email, setEmail] = useState("");

  const handleGrant = () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      return;
    }
    setAdmins((current) => [
      {
        id: `local-${current.length + 1}`,
        name: trimmedEmail.split("@")[0],
        email: trimmedEmail,
        grantedAt: "Just now",
      },
      ...current,
    ]);
    setEmail("");
    setGrantOpen(false);
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
            key: "granted",
            header: t(I18nKey.SUPER_ADMIN$COL_GRANTED),
            render: (row) => row.grantedAt,
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
                    onSelect: () =>
                      setAdmins((current) =>
                        current.length <= 1
                          ? current
                          : current.filter((admin) => admin.id !== row.id),
                      ),
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
  const [emailEnabled, setEmailEnabled] = useState(true);
  const [autoOrg, setAutoOrg] = useState(false);
  const { visible: setupVisible } = useSuperAdminSetup();

  return (
    <div className="flex flex-col gap-6" data-testid="super-admin-instance">
      <p className="text-sm text-[var(--oh-muted)]">
        {t(I18nKey.SUPER_ADMIN$INSTANCE_HINT)}
      </p>
      <SettingsSwitch isToggled={emailEnabled} onToggle={setEmailEnabled}>
        {t(I18nKey.SUPER_ADMIN$INSTANCE_EMAIL)}
      </SettingsSwitch>
      <SettingsSwitch isToggled={autoOrg} onToggle={setAutoOrg}>
        {t(I18nKey.SUPER_ADMIN$INSTANCE_AUTO_ORG)}
      </SettingsSwitch>
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
