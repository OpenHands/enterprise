import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { FaChevronLeft } from "react-icons/fa6";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { Dropdown } from "#/ui/dropdown/dropdown";
import { Pagination } from "#/ui/pagination";
import {
  useAuthProviders,
  useAuthProviderUsers,
} from "#/hooks/query/use-auth-providers";
import { useUpdateAuthProviderUserLink } from "#/hooks/mutation/use-auth-provider-mutations";
import { useSuperAdminUsers } from "#/hooks/query/use-super-admin";
import { useDebounce } from "#/hooks/use-debounce";
import { I18nKey } from "#/i18n/declaration";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { cn } from "#/utils/utils";
import type { AuthProviderUserLink } from "#/api/auth-providers-service/auth-providers.types";
import {
  BrandButton,
  SuperAdminPageHeader,
  SuperAdminSearchField,
  SuperAdminTable,
} from "./super-admin-chrome";

const LIMIT = 10;

/** ``Name <email>`` -- the form used everywhere on this page, because a
 * super admin re-pointing a link has to tell apart accounts that may share a
 * name or an email domain. Falls back through whichever parts exist. */
function formatAccount(
  name: string | null | undefined,
  email: string | null | undefined,
  fallbackId: string,
): string {
  const label = name?.trim();
  if (label && email) {
    return `${label} <${email}>`;
  }
  return label || email || fallbackId;
}

/**
 * Super Admin "User Links" table for one auth provider -- lets a super
 * admin re-point an ``auth_provider_users`` row to a different, already
 * existing account when the linked git/SSO identity's email has drifted
 * from the user it still resolves to (see
 * ``server.routes.oauth_providers_admin.update_auth_provider_user``).
 */
export function SuperAdminAuthProviderUsers() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { providerId } = useParams<{ providerId: string }>();
  const id = Number(providerId);

  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState("");
  const debouncedFilter = useDebounce(filter, 300);

  const { data: providers } = useAuthProviders();
  const provider = providers?.find((candidate) => candidate.id === id);

  const { data, isLoading, isError } = useAuthProviderUsers({
    providerId: id,
    page,
    limit: LIMIT,
    q: debouncedFilter || undefined,
  });

  const updateLink = useUpdateAuthProviderUserLink(id);

  const [editingLink, setEditingLink] = useState<AuthProviderUserLink | null>(
    null,
  );
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);

  // Only fetch the instance's full user directory once the "re-point to"
  // dropdown actually needs it, rather than on every page load.
  const { data: directoryUsers } = useSuperAdminUsers(editingLink !== null);

  // Pending invitations (``user_id === null``) have no account to point at
  // yet. Re-pointing onto your *own* account is allowed -- the same person
  // commonly ends up with more than one ``User`` row (e.g. one created with
  // a password, another auto-created the first time they signed in through
  // this provider because the two emails didn't match), and merging the
  // identity onto whichever row they're currently signed in as is exactly
  // the drift this page exists to fix. The label carries both name and email
  // because two accounts created for the same person typically differ in
  // exactly one of those.
  const userOptions = useMemo(
    () =>
      (directoryUsers ?? [])
        .filter((user) => user.user_id)
        .map((user) => ({
          value: user.user_id as string,
          label: formatAccount(user.name, user.email, user.user_id as string),
        })),
    [directoryUsers],
  );

  const items = data?.items ?? [];
  const totalPages = data ? Math.ceil(data.total / LIMIT) : 0;

  const openEdit = (link: AuthProviderUserLink) => {
    setEditingLink(link);
    setSelectedUserId(null);
  };

  const closeEdit = () => {
    if (updateLink.isPending) {
      return;
    }
    setEditingLink(null);
    setSelectedUserId(null);
  };

  const confirmEdit = () => {
    if (!editingLink || !selectedUserId) {
      return;
    }
    updateLink.mutate(
      { linkId: editingLink.id, userId: selectedUserId },
      { onSuccess: closeEdit },
    );
  };

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="super-admin-auth-provider-users"
    >
      <button
        type="button"
        data-testid="super-admin-auth-provider-users-back"
        onClick={() => navigate(SUPER_ADMIN_PATHS.authProviders)}
        className={cn(
          "inline-flex w-fit shrink-0 items-center gap-1.5",
          "text-xs font-medium text-[var(--oh-muted)] hover:text-white",
          "transition-colors",
        )}
      >
        <FaChevronLeft size={10} aria-hidden="true" />
        {t(I18nKey.SUPER_ADMIN$BACK_TO_AUTH_PROVIDERS)}
      </button>
      <SuperAdminPageHeader
        title={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS)}
        subtitle={
          provider ? (
            <Trans
              i18nKey={I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_SUBTITLE}
              values={{ title: provider.title }}
            />
          ) : (
            t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_SUBLINE)
          )
        }
      />
      <SuperAdminSearchField
        testId="super-admin-auth-provider-users-search"
        value={filter}
        placeholder={t(
          I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_SEARCH_PLACEHOLDER,
        )}
        onChange={(value) => {
          setFilter(value);
          setPage(1);
        }}
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
      <SuperAdminTable<AuthProviderUserLink>
        testId="super-admin-auth-provider-users-table"
        rows={items}
        getRowKey={(row) => String(row.id)}
        empty={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_EMPTY)}
        columns={[
          {
            key: "user",
            header: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_COL_USER),
            render: (row) => (
              <div className="flex min-w-0 flex-col">
                <span className="block truncate text-white">
                  {formatAccount(row.user_name, row.email, row.user_id)}
                </span>
                <span className="block truncate text-xs text-[var(--oh-muted)]">
                  {row.user_id}
                </span>
              </div>
            ),
          },
          {
            key: "external",
            header: t(
              I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_COL_EXTERNAL,
            ),
            render: (row) => (
              <div className="flex min-w-0 flex-col">
                <span className="block truncate text-white">
                  {row.external_email || row.external_subject_id}
                </span>
                <span className="block truncate text-xs text-[var(--oh-muted)]">
                  {row.external_email
                    ? row.external_subject_id
                    : t(
                        I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_NO_EXTERNAL_EMAIL,
                      )}
                </span>
              </div>
            ),
          },
          {
            key: "actions",
            header: "",
            className: "w-24 text-right",
            render: (row) => (
              <BrandButton
                type="button"
                variant="secondary"
                testId={`super-admin-auth-provider-user-edit-${row.id}`}
                className="h-7 min-h-7 px-2.5 text-xs"
                onClick={() => openEdit(row)}
              >
                {t(I18nKey.BUTTON$EDIT)}
              </BrandButton>
            ),
          },
        ]}
      />
      {totalPages > 1 ? (
        <Pagination
          currentPage={page}
          totalPages={totalPages}
          onPageChange={setPage}
        />
      ) : null}
      {editingLink ? (
        <OrgModal
          testId="super-admin-auth-provider-user-edit-modal"
          title={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_UPDATE_TITLE)}
          description={t(
            I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_UPDATE_HINT,
          )}
          primaryButtonText={t(
            I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_UPDATE_BUTTON,
          )}
          secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
          primaryButtonTestId="super-admin-auth-provider-user-edit-confirm"
          secondaryButtonTestId="super-admin-auth-provider-user-edit-cancel"
          isPrimaryDisabled={!selectedUserId}
          isLoading={updateLink.isPending}
          onPrimaryClick={confirmEdit}
          onClose={closeEdit}
        >
          {/* Restate what is being moved and where it points today: the admin
              picks from a list of every account on the instance, so the
              dialog has to keep the identity and the current target visible. */}
          <div className="flex flex-col gap-2 rounded-md border border-[var(--oh-border)] p-3 text-xs">
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="text-[var(--oh-muted)]">
                {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_EXTERNAL_LABEL)}
              </span>
              <span className="break-all text-white">
                {editingLink.external_email || editingLink.external_subject_id}
              </span>
              {editingLink.external_email ? (
                <span className="break-all text-[var(--oh-muted)]">
                  {editingLink.external_subject_id}
                </span>
              ) : null}
            </div>
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="text-[var(--oh-muted)]">
                {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_CURRENT_LABEL)}
              </span>
              <span className="break-all text-white">
                {formatAccount(
                  editingLink.user_name,
                  editingLink.email,
                  editingLink.user_id,
                )}
              </span>
            </div>
          </div>
          <Dropdown
            testId="super-admin-auth-provider-user-edit-select"
            options={userOptions}
            placeholder={t(
              I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_SELECT_PLACEHOLDER,
            )}
            disabled={updateLink.isPending}
            onChange={(item) =>
              setSelectedUserId((item?.value as string | undefined) ?? null)
            }
          />
        </OrgModal>
      ) : null}
    </div>
  );
}
