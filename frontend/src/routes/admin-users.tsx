import { useState } from "react";
import { Navigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useCanManageUsers } from "#/hooks/query/use-native-profile";
import {
  useNativeAccounts,
  useNativeAccount,
  useNativeInvitations,
  useNativeSuperadmins,
  useNativeRoles,
  useNativeInvitationOrganizations,
} from "#/hooks/query/use-native-accounts";
import {
  useIssueAccountInvitation,
  useReissueAccountInvitation,
  useRevokeAccountInvitation,
  useIssuePasswordReset,
  useChangeAccountState,
  useSetSuperadmin,
} from "#/hooks/mutation/use-native-auth";
import {
  AccountLink,
  NativeAccount,
  NativeAuthError,
} from "#/api/native-auth-service/native-auth-service.api";
import { AccountLinkModal } from "#/components/features/admin-users/account-link-modal";
import {
  AuthError,
  NativeLoginForm,
} from "#/components/features/native-auth/auth-form";
import { BrandButton } from "#/components/features/settings/brand-button";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { Typography } from "#/ui/typography";
import { Pagination } from "#/ui/pagination";
import { useConfig } from "#/hooks/query/use-config";

type AccountAction = "enable" | "disable" | "delete" | "grant" | "revoke";

export default function AdminUsers(): React.JSX.Element {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const {
    data: profile,
    canManageUsers,
    isPending: profilePending,
    isError: profileError,
  } = useCanManageUsers();
  const client = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [invitationOffset, setInvitationOffset] = useState(0);
  const accounts = useNativeAccounts(offset);
  const invitations = useNativeInvitations(invitationOffset);
  const { data: superadmins } = useNativeSuperadmins();
  const { data: roles } = useNativeRoles();
  const organizations = useNativeInvitationOrganizations();
  const [orgId, setOrgId] = useState("");
  const [roleId, setRoleId] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: selected } = useNativeAccount(selectedId);
  const [link, setLink] = useState<AccountLink | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{
    account: NativeAccount;
    action: AccountAction;
  } | null>(null);
  const [resetAccount, setResetAccount] = useState<string | null>(null);
  const issueInvitation = useIssueAccountInvitation();
  const reissue = useReissueAccountInvitation();
  const revokeInvitation = useRevokeAccountInvitation();
  const issueReset = useIssuePasswordReset();
  const lifecycle = useChangeAccountState();
  const setSuperadmin = useSetSuperadmin();
  const busy =
    issueInvitation.isPending ||
    reissue.isPending ||
    revokeInvitation.isPending ||
    issueReset.isPending ||
    lifecycle.isPending ||
    setSuperadmin.isPending;

  const refresh = async (): Promise<void> => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["native-accounts"] }),
      client.invalidateQueries({ queryKey: ["native-invitations"] }),
      client.invalidateQueries({ queryKey: ["native-superadmins"] }),
      client.invalidateQueries({ queryKey: ["native-profile"] }),
    ]);
  };
  const report = (cause: unknown): void =>
    setError(
      cause instanceof Error ? cause.message : t("NATIVE_AUTH$REQUEST_FAILED"),
    );
  const createInvitation = async (
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setError(null);
    try {
      setLink(
        await issueInvitation.run({
          email: String(fields.get("email")),
          ...(orgId ? { org_id: orgId, org_role_id: roleId ?? undefined } : {}),
        }),
      );
      form.reset();
      setOrgId("");
      setRoleId(null);
      await refresh();
    } catch (cause) {
      report(cause);
    }
  };
  const resetPassword = async (id: string): Promise<void> => {
    setError(null);
    try {
      setLink(await issueReset.run(id));
      setResetAccount(null);
    } catch (cause) {
      if (
        cause instanceof NativeAuthError &&
        [401, 403].includes(cause.status) &&
        /recent|authentication|browser|sign in again/i.test(cause.message)
      )
        setResetAccount(id);
      else report(cause);
    }
  };
  const performAction = async (): Promise<void> => {
    if (!confirm) return;
    setError(null);
    setNotice(null);
    try {
      if (confirm.action === "grant" || confirm.action === "revoke")
        await setSuperadmin.run({
          id: confirm.account.id,
          enabled: confirm.action === "grant",
        });
      else {
        const result = await lifecycle.run({
          id: confirm.account.id,
          action: confirm.action,
        });
        if (result.warnings?.length) setNotice(result.warnings.join(" "));
      }
      setConfirm(null);
      await refresh();
    } catch (cause) {
      setConfirm(null);
      report(cause);
    }
  };
  const accountStatus = (account: NativeAccount): string => {
    if (account.state === "deleted") return t("NATIVE_AUTH$DELETED");
    if (account.is_disabled) return t("NATIVE_AUTH$DISABLED");
    if (!account.profile_present)
      return t(
        account.state === "reonboardable"
          ? "NATIVE_AUTH$PROFILE_ABSENT"
          : "NATIVE_AUTH$PROFILE_UNAVAILABLE",
      );
    return t("NATIVE_AUTH$ACTIVE");
  };
  const actionLabel = (action: AccountAction): string =>
    t(
      {
        enable: "NATIVE_AUTH$ENABLE",
        disable: "NATIVE_AUTH$DISABLE",
        delete: "NATIVE_AUTH$DELETE",
        grant: "NATIVE_AUTH$GRANT_ADMIN",
        revoke: "NATIVE_AUTH$REVOKE_ADMIN",
      }[action],
    );

  if (config?.auth_mode !== "native")
    return <Navigate to="/settings" replace />;
  if (profilePending) return <p>{t("HOME$LOADING")}</p>;
  if (profileError)
    return <p role="alert">{t("NATIVE_AUTH$REQUEST_FAILED")}</p>;
  if (!canManageUsers) return <Navigate to="/settings" replace />;

  return (
    <div className="flex flex-col gap-8 pb-8">
      <p>{t("NATIVE_AUTH$USERS_HELP")}</p>
      <AuthError message={error} />
      {notice && <p role="status">{notice}</p>}
      <form
        onSubmit={createInvitation}
        className="max-w-[680px] flex flex-col gap-4 pb-8 border-b border-tertiary"
      >
        <Typography.H3 className="text-xl">
          {t("NATIVE_AUTH$INVITE_USER")}
        </Typography.H3>
        {organizations.isError && (
          <AuthError message={t("NATIVE_AUTH$REQUEST_FAILED")} />
        )}
        <SettingsInput
          className="w-full"
          label={t("NATIVE_AUTH$EMAIL")}
          type="email"
          name="email"
          required
        />
        <SettingsDropdownInput
          testId="invitation-organization"
          name="organization"
          label={t("NATIVE_AUTH$OPTIONAL_ORG")}
          selectedKey={orgId || "personal"}
          isClearable={false}
          isDisabled={organizations.isPending || organizations.isError}
          items={[
            {
              key: "personal",
              label: t("NATIVE_AUTH$PERSONAL_WORKSPACE_ONLY"),
            },
            ...(organizations.data?.map((org) => ({
              key: org.id,
              label: org.name,
            })) ?? []),
          ]}
          onSelectionChange={(key): void => {
            if (typeof key !== "string") return;
            setOrgId(key === "personal" ? "" : key);
            setRoleId(null);
          }}
        />
        {orgId && (
          <SettingsDropdownInput
            testId="invitation-role"
            name="role"
            required
            isClearable={false}
            label={t("NATIVE_AUTH$ORG_ROLE")}
            placeholder={t("NATIVE_AUTH$SELECT_ROLE")}
            selectedKey={roleId === null ? null : String(roleId)}
            items={
              roles?.map((role) => ({
                key: String(role.id),
                label: role.name,
              })) ?? []
            }
            onSelectionChange={(key): void => {
              const selectedRole = roles?.find(
                (role) => String(role.id) === key,
              );
              setRoleId(selectedRole?.id ?? null);
            }}
          />
        )}
        <BrandButton
          type="submit"
          variant="primary"
          isDisabled={busy || (!!orgId && roleId === null)}
        >
          {t("NATIVE_AUTH$CREATE_SETUP_LINK")}
        </BrandButton>
      </form>
      <section className="flex flex-col gap-4">
        <Typography.H3 className="text-xl">
          {t("NATIVE_AUTH$ACCOUNTS")}
        </Typography.H3>
        {(accounts.isError || invitations.isError) && (
          <AuthError message={t("NATIVE_AUTH$REQUEST_FAILED")} />
        )}
        {accounts.isPending && <p>{t("HOME$LOADING")}</p>}
        {accounts.data?.items.map((account) => (
          <div
            key={account.id}
            className="flex items-center justify-between gap-3 border-b border-tertiary py-3"
          >
            <button
              type="button"
              onClick={() => setSelectedId(account.id)}
              className="text-left underline text-primary"
            >
              {account.email || account.id}
            </button>
            <span>{accountStatus(account)}</span>
          </div>
        ))}
        {accounts.data?.total === 0 && <p>{t("NATIVE_AUTH$NO_ACCOUNTS")}</p>}
        <Pagination
          currentPage={offset / 50 + 1}
          totalPages={Math.ceil((accounts.data?.total || 0) / 50)}
          onPageChange={(page): void => setOffset((page - 1) * 50)}
        />
      </section>
      {selected && (
        <section className="flex flex-col gap-4 py-5 border-y border-tertiary">
          <Typography.H3 className="text-xl">
            {selected.email || selected.id}
          </Typography.H3>
          <p>{accountStatus(selected)}</p>
          <p className="text-sm text-tertiary-alt">{selected.id}</p>
          {selected.state !== "deleted" && (
            <div className="flex flex-wrap gap-3">
              <BrandButton
                type="button"
                variant="secondary"
                isDisabled={busy}
                onClick={() =>
                  setConfirm({
                    account: selected,
                    action: selected.is_disabled ? "enable" : "disable",
                  })
                }
              >
                {actionLabel(selected.is_disabled ? "enable" : "disable")}
              </BrandButton>
              {!selected.is_disabled &&
                (selected.authentication_methods?.includes("password") ??
                  true) && (
                  <BrandButton
                    type="button"
                    variant="secondary"
                    isDisabled={busy}
                    onClick={() => resetPassword(selected.id)}
                  >
                    {t("NATIVE_AUTH$CREATE_RESET_LINK")}
                  </BrandButton>
                )}
              {!selected.is_disabled &&
                selected.profile_present &&
                profile?.global_permissions.includes("manage_super_admins") && (
                  <BrandButton
                    type="button"
                    variant="secondary"
                    isDisabled={busy || !superadmins}
                    onClick={() =>
                      setConfirm({
                        account: selected,
                        action: superadmins?.super_admins.some(
                          (admin) => admin.user_id === selected.id,
                        )
                          ? "revoke"
                          : "grant",
                      })
                    }
                  >
                    {actionLabel(
                      superadmins?.super_admins.some(
                        (admin) => admin.user_id === selected.id,
                      )
                        ? "revoke"
                        : "grant",
                    )}
                  </BrandButton>
                )}
              <BrandButton
                type="button"
                variant="danger"
                isDisabled={busy}
                onClick={() =>
                  setConfirm({ account: selected, action: "delete" })
                }
              >
                {actionLabel("delete")}
              </BrandButton>
            </div>
          )}
          {selected.pending_invitations.map((invitation) => (
            <p key={invitation.id}>
              {t("NATIVE_AUTH$PENDING_SETUP", {
                date: new Date(invitation.expires_at).toLocaleString(),
              })}
            </p>
          ))}
        </section>
      )}
      <section className="flex flex-col gap-4">
        <Typography.H3 className="text-xl">
          {t("NATIVE_AUTH$INVITATIONS")}
        </Typography.H3>
        {invitations.data?.items.map((invitation) => {
          const pending = !invitation.consumed_at && !invitation.revoked_at;
          const expired =
            new Date(invitation.expires_at).getTime() <= Date.now();
          let statusKey = "NATIVE_AUTH$PENDING";
          if (expired) statusKey = "NATIVE_AUTH$EXPIRED";
          if (invitation.revoked_at) statusKey = "NATIVE_AUTH$REVOKED";
          if (invitation.consumed_at) statusKey = "NATIVE_AUTH$ACCEPTED";
          return (
            <div
              key={invitation.id}
              className="border-b border-tertiary py-3 flex flex-wrap items-center justify-between gap-3"
            >
              <div>
                <p>{invitation.email}</p>
                <p className="text-sm text-tertiary-alt">{t(statusKey)}</p>
              </div>
              {pending && (
                <div className="flex gap-3">
                  <BrandButton
                    type="button"
                    variant="secondary"
                    isDisabled={busy}
                    onClick={async () => {
                      setError(null);
                      try {
                        setLink(await reissue.run(invitation.id));
                        await refresh();
                      } catch (cause) {
                        report(cause);
                      }
                    }}
                  >
                    {t("NATIVE_AUTH$REISSUE")}
                  </BrandButton>
                  <BrandButton
                    type="button"
                    variant="ghost-danger"
                    isDisabled={busy}
                    onClick={async () => {
                      setError(null);
                      try {
                        await revokeInvitation.run(invitation.id);
                        await refresh();
                      } catch (cause) {
                        report(cause);
                      }
                    }}
                  >
                    {t("NATIVE_AUTH$REVOKE")}
                  </BrandButton>
                </div>
              )}
            </div>
          );
        })}
        {invitations.data?.total === 0 && (
          <p>{t("NATIVE_AUTH$NO_INVITATIONS")}</p>
        )}
        <Pagination
          currentPage={invitationOffset / 50 + 1}
          totalPages={Math.ceil((invitations.data?.total || 0) / 50)}
          onPageChange={(page): void => setInvitationOffset((page - 1) * 50)}
        />
      </section>
      {link && <AccountLinkModal link={link} onClose={() => setLink(null)} />}
      {confirm && (
        <OrgModal
          title={actionLabel(confirm.action)}
          ariaLabel={actionLabel(confirm.action)}
          description={t(
            confirm.action === "delete"
              ? "NATIVE_AUTH$DELETE_HELP"
              : "NATIVE_AUTH$ADMIN_ACTION_HELP",
          )}
          primaryButtonText={t("NATIVE_AUTH$CONFIRM")}
          secondaryButtonText={t("NATIVE_AUTH$CANCEL")}
          onPrimaryClick={performAction}
          onClose={(): void => setConfirm(null)}
          isLoading={busy}
          className="max-w-full"
        >
          <p className="text-sm">
            {confirm.account.email || confirm.account.id}
          </p>
        </OrgModal>
      )}
      {resetAccount && (
        <OrgModal
          title={t("NATIVE_AUTH$REAUTH_REQUIRED")}
          ariaLabel={t("NATIVE_AUTH$REAUTH_REQUIRED")}
          primaryButtonText={t("NATIVE_AUTH$CANCEL")}
          hideSecondaryButton
          onPrimaryClick={(): void => setResetAccount(null)}
          onClose={(): void => setResetAccount(null)}
          className="max-w-full"
        >
          <NativeLoginForm
            email={profile?.email}
            reauthenticate
            onSuccess={() => resetPassword(resetAccount)}
          />
        </OrgModal>
      )}
    </div>
  );
}
