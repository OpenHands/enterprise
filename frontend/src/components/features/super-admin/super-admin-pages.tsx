import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { CreateOrganizationModal } from "#/components/features/org/create-organization-modal";
import { InviteOrganizationMemberModal } from "#/components/features/org/invite-organization-member-modal";
import { MintSignupLinkModal } from "#/components/features/org/mint-signup-link-modal";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useConfig } from "#/hooks/query/use-config";
import { useMe } from "#/hooks/query/use-me";
import {
  useDeleteSuperAdminOrganization,
  useGrantSuperAdmin,
  useRevokeSuperAdmin,
  useUpdateSetupState,
  useUpdateSuperAdminOrganizationStatus,
} from "#/hooks/mutation/use-super-admin-mutations";
import {
  useSetupState,
  useSuperAdminOrganizations,
  useSuperAdmins,
  useSuperAdminUsers,
} from "#/hooks/query/use-super-admin";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import { HelpLink } from "#/ui/help-link";
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
import { InstanceLogoSetting } from "./instance-logo-setting";
import { SuperAdminUserGroupsModal } from "./super-admin-user-groups-modal";
import { SuperAdminSetupGuide } from "./super-admin-setup-guide";
import type {
  SuperAdminAdminRow,
  SuperAdminMembership,
  SuperAdminOrgRole,
  SuperAdminOrgRow,
  SuperAdminUserRow,
} from "./super-admin-types";
import { SuperAdminGrantSelfAccessModal } from "./super-admin-grant-self-access-modal";
import { useSuperAdminViewOrg } from "./use-super-admin-view-org";

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
  const {
    viewOrg,
    canViewOrg,
    pendingOrg,
    dismissGrant,
    confirmGrant,
    userId,
  } = useSuperAdminViewOrg();
  const copy = navCopy(SUPER_ADMIN_PATHS.organizations);
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [inviteOrgId, setInviteOrgId] = useState<string | null>(null);
  const [pendingOrgAction, setPendingOrgAction] = useState<{
    action: "suspend" | "remove";
    org: SuperAdminOrgRow;
  } | null>(null);
  const { data, isLoading, isError } = useSuperAdminOrganizations();
  const deleteOrg = useDeleteSuperAdminOrganization();
  const updateOrgStatus = useUpdateSuperAdminOrganizationStatus();

  const orgs: SuperAdminOrgRow[] = useMemo(
    () =>
      (data ?? []).map((org) => ({
        id: org.id,
        // A personal workspace is stored as user_<id>_org; show the name the
        // rest of the app uses.
        name: org.is_personal ? t(I18nKey.ORG$PERSONAL_WORKSPACE) : org.name,
        members: org.member_count,
        status: org.status === "suspended" ? "suspended" : "active",
        contactEmail: org.contact_email ?? "",
        isPersonal: org.is_personal,
      })),
    [data, t],
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

  const confirmOrgAction = () => {
    if (!pendingOrgAction) {
      return;
    }
    const orgId = pendingOrgAction.org.id;
    const onSuccess = () => setPendingOrgAction(null);
    if (pendingOrgAction.action === "remove") {
      deleteOrg.mutate({ orgId }, { onSuccess });
    } else {
      updateOrgStatus.mutate({ orgId, status: "suspended" }, { onSuccess });
    }
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
            testId="super-admin-create-org"
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
            render: (row) =>
              canViewOrg(row.id) ? (
                <button
                  type="button"
                  data-testid={`super-admin-org-open-${row.id}`}
                  className="block max-w-full truncate text-left hover:underline"
                  onClick={() => viewOrg(row.id, row.name)}
                >
                  {row.name}
                </button>
              ) : (
                <span className="block truncate">{row.name}</span>
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
            render: (row) =>
              canViewOrg(row.id) ? (
                <SuperAdminRowMenu
                  testId={`super-admin-org-actions-${row.id}`}
                  ariaLabel={t(I18nKey.SUPER_ADMIN$ROW_ACTIONS)}
                  items={[
                    {
                      label: t(I18nKey.SUPER_ADMIN$VIEW_ORG),
                      testId: `super-admin-org-view-${row.id}`,
                      onSelect: () => viewOrg(row.id, row.name),
                    },
                    // Personal workspaces belong to their user; the dashboard
                    // does not suspend or delete them.
                    ...(row.isPersonal
                      ? []
                      : [
                          {
                            label: t(I18nKey.SUPER_ADMIN$INVITE_BY_EMAIL),
                            testId: `super-admin-org-invite-${row.id}`,
                            onSelect: () => setInviteOrgId(row.id),
                          },
                          row.status === "active"
                            ? {
                                label: t(I18nKey.SUPER_ADMIN$SUSPEND),
                                testId: `super-admin-org-suspend-${row.id}`,
                                onSelect: () =>
                                  setPendingOrgAction({
                                    action: "suspend",
                                    org: row,
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
                            onSelect: () =>
                              setPendingOrgAction({
                                action: "remove",
                                org: row,
                              }),
                          },
                        ]),
                  ]}
                />
              ) : null,
          },
        ]}
      />
      {createOpen && (
        <CreateOrganizationModal
          contactEmail={me?.email}
          onClose={() => setCreateOpen(false)}
        />
      )}
      {inviteOrgId ? (
        <InviteOrganizationMemberModal
          organizations={orgs
            .filter((org) => !org.isPersonal)
            .map((org) => ({ id: org.id, name: org.name }))}
          defaultOrgId={inviteOrgId}
          onClose={() => setInviteOrgId(null)}
        />
      ) : null}
      {pendingOrgAction ? (
        <OrgModal
          testId="super-admin-org-confirm"
          title={
            pendingOrgAction.action === "remove"
              ? t(I18nKey.ORG$DELETE_ORGANIZATION)
              : t(I18nKey.SUPER_ADMIN$SUSPEND_ORG_TITLE)
          }
          description={
            <Trans
              i18nKey={
                pendingOrgAction.action === "remove"
                  ? I18nKey.ORG$DELETE_ORGANIZATION_WARNING_WITH_NAME
                  : I18nKey.SUPER_ADMIN$SUSPEND_ORG_CONFIRM
              }
              values={{ name: pendingOrgAction.org.name }}
              components={{ name: <span className="text-white" /> }}
            />
          }
          primaryButtonText={t(I18nKey.BUTTON$CONFIRM)}
          secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
          primaryButtonTestId="super-admin-org-confirm-submit"
          secondaryButtonTestId="super-admin-org-confirm-cancel"
          onPrimaryClick={confirmOrgAction}
          onClose={() => setPendingOrgAction(null)}
          isLoading={deleteOrg.isPending || updateOrgStatus.isPending}
        />
      ) : null}
      {pendingOrg ? (
        <SuperAdminGrantSelfAccessModal
          orgId={pendingOrg.orgId}
          orgName={pendingOrg.orgName}
          userId={userId}
          onClose={dismissGrant}
          onGranted={confirmGrant}
        />
      ) : null}
    </div>
  );
}

export function SuperAdminUsers() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  // The only account-creation mechanism this page offers is a sign-up link
  // for the local password IDP (see ``MintSignupLinkModal``) -- with no
  // integrated IDP there is no password-based account for the recipient to
  // set up, so the affordance is hidden rather than offering a link that
  // can never be used.
  const enableIntegratedIdp = !!config?.feature_flags?.enable_integrated_idp;
  const {
    viewOrg,
    canViewOrg,
    pendingOrg,
    dismissGrant,
    confirmGrant,
    userId,
  } = useSuperAdminViewOrg();
  const copy = navCopy(SUPER_ADMIN_PATHS.users);
  const [query, setQuery] = useState("");
  const [mintLinkOpen, setMintLinkOpen] = useState(false);
  const [managedUserId, setManagedUserId] = useState<string | null>(null);
  const { data, isLoading, isError } = useSuperAdminUsers();
  const { data: orgs } = useSuperAdminOrganizations();

  const users: SuperAdminUserRow[] = useMemo(
    () =>
      (data ?? []).map((user) => {
        const memberships: SuperAdminMembership[] = user.memberships.map(
          (membership) => ({
            orgId: membership.org_id,
            // A personal workspace shares its owner's id and is stored as
            // user_<id>_org; show the name the rest of the app uses.
            orgName:
              membership.org_id === user.user_id
                ? t(I18nKey.ORG$PERSONAL_WORKSPACE)
                : membership.org_name,
            role: toOrgRole(membership.role),
            status: membership.status,
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
    [data, t],
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

  const managedUser = users.find((user) => user.id === managedUserId) ?? null;

  return (
    <div className="flex flex-col gap-4" data-testid="super-admin-users">
      <SuperAdminPageHeader
        title={t(copy.text)}
        subtitle={t(copy.subtitle)}
        action={
          enableIntegratedIdp ? (
            <BrandButton
              type="button"
              variant="primary"
              testId="super-admin-create-signup-link"
              startContent={<Plus className="h-4 w-4" />}
              onClick={() => setMintLinkOpen(true)}
            >
              {t(I18nKey.ORG$CREATE_SIGNUP_LINK)}
            </BrandButton>
          ) : undefined
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
        onRowClick={(row) => setManagedUserId(row.id)}
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
                onOrgClick={viewOrg}
                canOpenOrg={canViewOrg}
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
                  {
                    label: t(I18nKey.SUPER_ADMIN$MANAGE_USER),
                    testId: `super-admin-manage-user-${row.id}`,
                    onSelect: () => setManagedUserId(row.id),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      {mintLinkOpen && (
        <MintSignupLinkModal
          orgId={null}
          organizations={teamOrgs.map((org) => ({
            id: org.id,
            name: org.name,
          }))}
          onClose={() => setMintLinkOpen(false)}
        />
      )}
      {managedUser ? (
        <SuperAdminUserGroupsModal
          user={managedUser}
          organizations={teamOrgs.map((org) => ({
            id: org.id,
            name: org.name,
          }))}
          isSelf={managedUser.id === userId}
          onClose={() => setManagedUserId(null)}
        />
      ) : null}
      {pendingOrg ? (
        <SuperAdminGrantSelfAccessModal
          orgId={pendingOrg.orgId}
          orgName={pendingOrg.orgName}
          userId={userId}
          onClose={dismissGrant}
          onGranted={confirmGrant}
        />
      ) : null}
    </div>
  );
}

export function SuperAdminAdmins() {
  const { t } = useTranslation();
  const { userId } = useSuperAdminViewOrg();
  const copy = navCopy(SUPER_ADMIN_PATHS.admins);
  const [grantOpen, setGrantOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [adminToRevoke, setAdminToRevoke] = useState<SuperAdminAdminRow | null>(
    null,
  );
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
            render: (row) => {
              const isSelf = row.id === userId;
              return (
                <SuperAdminRowMenu
                  testId={`super-admin-admin-actions-${row.id}`}
                  ariaLabel={t(I18nKey.SUPER_ADMIN$ROW_ACTIONS)}
                  items={[
                    {
                      label: t(I18nKey.SUPER_ADMIN$REVOKE),
                      testId: `super-admin-admin-revoke-${row.id}`,
                      destructive: true,
                      // Nobody may revoke their own Super Admin access --
                      // not even when other Super Admins exist -- so they
                      // can never accidentally lock themselves out.
                      isDisabled: isSelf,
                      title: isSelf
                        ? t(I18nKey.SUPER_ADMIN$CANNOT_REVOKE_SELF)
                        : undefined,
                      onSelect: () => setAdminToRevoke(row),
                    },
                  ]}
                />
              );
            },
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
      {adminToRevoke ? (
        <OrgModal
          testId="super-admin-revoke-confirm"
          title={t(I18nKey.SUPER_ADMIN$REVOKE_ADMIN_TITLE)}
          description={
            <Trans
              i18nKey={I18nKey.SUPER_ADMIN$REVOKE_ADMIN_CONFIRM}
              values={{ name: adminToRevoke.email || adminToRevoke.name }}
              components={{ name: <span className="text-white" /> }}
            />
          }
          primaryButtonText={t(I18nKey.BUTTON$CONFIRM)}
          secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
          primaryButtonTestId="super-admin-revoke-confirm-submit"
          secondaryButtonTestId="super-admin-revoke-confirm-cancel"
          onPrimaryClick={() =>
            revoke.mutate(
              { userId: adminToRevoke.id },
              { onSuccess: () => setAdminToRevoke(null) },
            )
          }
          onClose={() => setAdminToRevoke(null)}
          isLoading={revoke.isPending}
        />
      ) : null}
    </div>
  );
}

const SAML_SSO_DOCS_URL =
  "https://docs.openhands.dev/enterprise/integrations/saml-sso";

export function SuperAdminInstance() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const emailEnabled = Boolean(config?.email_enabled);
  const personalWorkspacesHidden =
    config?.feature_flags?.hide_personal_workspaces === true;
  const { data: setupState } = useSetupState();
  const { mutate: updateSetupState } = useUpdateSetupState();
  const hasGuide = Boolean(setupState?.guide_org_id);

  return (
    <div className="flex flex-col gap-6" data-testid="super-admin-instance">
      <InstanceLogoSetting />
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
        <SettingsSwitch
          isToggled={!personalWorkspacesHidden}
          isDisabled
          onToggle={() => undefined}
        >
          {t(I18nKey.SUPER_ADMIN$INSTANCE_AUTO_ORG)}
        </SettingsSwitch>
        <p className="text-xs text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$INSTANCE_AUTO_ORG_HINT)}
        </p>
      </div>
      <div
        className="flex flex-col gap-1"
        data-testid="super-admin-instance-sso"
      >
        <p className="text-sm text-white">
          {t(I18nKey.SUPER_ADMIN$INSTANCE_SSO)}
        </p>
        <HelpLink
          testId="super-admin-instance-sso-docs"
          text={t(I18nKey.SUPER_ADMIN$INSTANCE_SSO_HINT)}
          linkText={t(I18nKey.SUPER_ADMIN$INSTANCE_SSO_DOCS)}
          href={SAML_SSO_DOCS_URL}
          linkColor="white"
          className="text-[var(--oh-muted)]"
        />
      </div>
      {/* Only the first Super Admin has a guide to show or hide. */}
      <SettingsSwitch
        testId="super-admin-instance-setup-guide"
        isToggled={hasGuide && !setupState?.guide_dismissed}
        isDisabled={!hasGuide}
        onToggle={(show) => updateSetupState({ guide_dismissed: !show })}
      >
        {t(I18nKey.SUPER_ADMIN$INSTANCE_SETUP_GUIDE)}
      </SettingsSwitch>
    </div>
  );
}
