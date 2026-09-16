import { useState } from "react";
import { Navigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthentication } from "#/hooks/use-authentication";
import { useCanManageUsers } from "#/hooks/query/use-native-profile";
import {
  useNativeAccounts,
  useNativeAccount,
  useNativeInvitations,
  useNativeRoles,
  useNativeInvitationOrganizations,
} from "#/hooks/query/use-native-accounts";
import {
  useIssueAccountInvitation,
  useReissueAccountInvitation,
  useRevokeAccountInvitation,
  useIssuePasswordReset,
} from "#/hooks/mutation/use-native-auth";
import {
  AccountLink,
  NativeAccount,
} from "#/api/native-auth-service/native-auth-service.api";
import { AccountLinkModal } from "#/components/features/admin-users/account-link-modal";
import { AuthError } from "#/components/features/native-auth/auth-form";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { Typography } from "#/ui/typography";
import { Pagination } from "#/ui/pagination";

export default function AdminUsers(): React.JSX.Element {
  const { t } = useTranslation();
  const authentication = useAuthentication();
  const {
    canManageUsers,
    isPending: profilePending,
    isError: profileError,
  } = useCanManageUsers();
  const client = useQueryClient();
  const [offset, setOffset] = useState(0);
  const [invitationOffset, setInvitationOffset] = useState(0);
  const accounts = useNativeAccounts(offset);
  const invitations = useNativeInvitations(invitationOffset);
  const { data: roles } = useNativeRoles();
  const organizations = useNativeInvitationOrganizations();
  const [orgId, setOrgId] = useState("");
  const [roleId, setRoleId] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: selected } = useNativeAccount(selectedId);
  const [link, setLink] = useState<AccountLink | null>(null);
  const [error, setError] = useState<string | null>(null);
  const issueInvitation = useIssueAccountInvitation();
  const reissue = useReissueAccountInvitation();
  const revokeInvitation = useRevokeAccountInvitation();
  const issueReset = useIssuePasswordReset();
  const busy =
    issueInvitation.isPending ||
    reissue.isPending ||
    revokeInvitation.isPending ||
    issueReset.isPending;

  const refresh = async (): Promise<void> => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["native-accounts"] }),
      client.invalidateQueries({ queryKey: ["native-invitations"] }),
      client.invalidateQueries({ queryKey: ["native-profile"] }),
    ]);
  };
  const report = (cause: unknown): void =>
    setError(cause instanceof Error ? cause.message : t("AUTH$REQUEST_FAILED"));
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
    } catch (cause) {
      report(cause);
    }
  };
  const accountStatus = (account: NativeAccount): string => {
    if (account.state === "deleted") return t("AUTH$DELETED");
    if (account.is_disabled) return t("AUTH$DISABLED");
    if (!account.profile_present)
      return t(
        account.state === "reonboardable"
          ? "AUTH$PROFILE_ABSENT"
          : "AUTH$PROFILE_UNAVAILABLE",
      );
    return t("AUTH$ACTIVE");
  };
  if (!authentication.accountActions.includes("manage"))
    return <Navigate to="/settings" replace />;
  if (profilePending) return <p>{t("HOME$LOADING")}</p>;
  if (profileError) return <p role="alert">{t("AUTH$REQUEST_FAILED")}</p>;
  if (!canManageUsers) return <Navigate to="/settings" replace />;

  return (
    <div className="flex flex-col gap-8 pb-8">
      <p>{t("AUTH$USERS_HELP")}</p>
      <AuthError message={error} />
      <form
        onSubmit={createInvitation}
        className="max-w-[680px] flex flex-col gap-4 pb-8 border-b border-tertiary"
      >
        <Typography.H3 className="text-xl">
          {t("AUTH$INVITE_USER")}
        </Typography.H3>
        {organizations.isError && (
          <AuthError message={t("AUTH$REQUEST_FAILED")} />
        )}
        <SettingsInput
          className="w-full"
          label={t("AUTH$EMAIL")}
          type="email"
          name="email"
          required
        />
        <SettingsDropdownInput
          testId="invitation-organization"
          name="organization"
          label={t("AUTH$OPTIONAL_ORG")}
          selectedKey={orgId || "personal"}
          isClearable={false}
          isDisabled={organizations.isPending || organizations.isError}
          items={[
            {
              key: "personal",
              label: t("AUTH$PERSONAL_WORKSPACE_ONLY"),
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
            label={t("AUTH$ORG_ROLE")}
            placeholder={t("AUTH$SELECT_ROLE")}
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
          {t("AUTH$CREATE_SETUP_LINK")}
        </BrandButton>
      </form>
      <section className="flex flex-col gap-4">
        <Typography.H3 className="text-xl">{t("AUTH$ACCOUNTS")}</Typography.H3>
        {(accounts.isError || invitations.isError) && (
          <AuthError message={t("AUTH$REQUEST_FAILED")} />
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
        {accounts.data?.total === 0 && <p>{t("AUTH$NO_ACCOUNTS")}</p>}
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
              {!selected.is_disabled &&
                (selected.authentication_methods?.includes("password") ??
                  true) && (
                  <BrandButton
                    type="button"
                    variant="secondary"
                    isDisabled={busy}
                    onClick={() => resetPassword(selected.id)}
                  >
                    {t("AUTH$CREATE_RESET_LINK")}
                  </BrandButton>
                )}
            </div>
          )}
          {selected.pending_invitations.map((invitation) => (
            <p key={invitation.id}>
              {t("AUTH$PENDING_SETUP", {
                date: new Date(invitation.expires_at).toLocaleString(),
              })}
            </p>
          ))}
        </section>
      )}
      <section className="flex flex-col gap-4">
        <Typography.H3 className="text-xl">
          {t("AUTH$INVITATIONS")}
        </Typography.H3>
        {invitations.data?.items.map((invitation) => {
          const pending = !invitation.consumed_at && !invitation.revoked_at;
          const expired =
            new Date(invitation.expires_at).getTime() <= Date.now();
          let statusKey = "AUTH$PENDING";
          if (expired) statusKey = "AUTH$EXPIRED";
          if (invitation.revoked_at) statusKey = "AUTH$REVOKED";
          if (invitation.consumed_at) statusKey = "AUTH$ACCEPTED";
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
                    {t("AUTH$REISSUE")}
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
                    {t("AUTH$REVOKE")}
                  </BrandButton>
                </div>
              )}
            </div>
          );
        })}
        {invitations.data?.total === 0 && <p>{t("AUTH$NO_INVITATIONS")}</p>}
        <Pagination
          currentPage={invitationOffset / 50 + 1}
          totalPages={Math.ceil((invitations.data?.total || 0) / 50)}
          onPageChange={(page): void => setInvitationOffset((page - 1) * 50)}
        />
      </section>
      {link && <AccountLinkModal link={link} onClose={() => setLink(null)} />}
    </div>
  );
}
