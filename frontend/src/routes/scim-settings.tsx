import React from "react";
import ReactDOM from "react-dom";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import {
  ScimCredential,
  ScimProviderOption,
} from "#/api/organization-service/scim.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { CreateScimCredentialModal } from "#/components/features/scim/create-scim-credential-modal";
import { ScimTokenModal } from "#/components/features/scim/scim-token-modal";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useScimCredentials, useScimSetup } from "#/hooks/query/use-scim";
import {
  useCreateScimCredential,
  useRevokeScimCredential,
} from "#/hooks/mutation/use-scim-credential-mutations";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import { cn } from "#/utils/utils";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import {
  settingsListContainerClassName,
  settingsListSectionHeaderClassName,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";

export const clientLoader = createPermissionGuard("provision_user");

export const handle = { hideTitle: true };

/** The one modal open at a time, or none. */
type ModalState =
  | { type: "create" }
  | { type: "token"; token: string }
  | { type: "revoke"; credential: ScimCredential };

const formatDate = (value: string) =>
  new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

/**
 * Whether revoking `credential` leaves its provider with no active credential,
 * which ends what lets an org admin create another one for it.
 */
const isLastActiveForProvider = (
  credential: ScimCredential,
  credentials: ScimCredential[],
) =>
  !credentials.some(
    (other) =>
      other.id !== credential.id &&
      other.revoked_at === null &&
      other.auth_provider_id === credential.auth_provider_id,
  );

function NoProvidersNotice({ canBind }: { canBind: boolean }) {
  const { t } = useTranslation();
  return (
    <p
      data-testid="scim-no-providers"
      className="rounded-lg border border-[var(--oh-border)] p-3 text-sm leading-5 text-muted"
    >
      {t(
        canBind
          ? I18nKey.SCIM$NO_PROVIDERS_SUPER_ADMIN
          : I18nKey.SCIM$NO_PROVIDERS_ORG_ADMIN,
      )}
    </p>
  );
}

function CredentialRow({
  credential,
  onRevoke,
}: {
  credential: ScimCredential;
  onRevoke: () => void;
}) {
  const { t } = useTranslation();
  const isRevoked = credential.revoked_at !== null;
  const details = [
    credential.auth_provider_title,
    t(I18nKey.SCIM$CREATED_AT, { date: formatDate(credential.created_at) }),
    credential.last_used_at
      ? t(I18nKey.SCIM$LAST_USED_AT, {
          date: formatDate(credential.last_used_at),
        })
      : t(I18nKey.SCIM$NEVER_USED),
    credential.revoked_at
      ? t(I18nKey.SCIM$REVOKED_AT, { date: formatDate(credential.revoked_at) })
      : null,
  ].filter(Boolean);

  return (
    <li
      data-testid="scim-credential-item"
      className={cn(
        settingsListTableRowClassName,
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-3 py-2.5",
      )}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={cn(
              "truncate text-sm font-normal leading-5",
              isRevoked ? "text-muted" : "text-white",
            )}
          >
            {credential.name}
          </span>
          <span
            data-testid="scim-credential-status"
            className="shrink-0 rounded-lg border border-[var(--oh-border)] px-2 py-0.5 text-xs text-muted"
          >
            {t(
              isRevoked ? I18nKey.SCIM$REVOKED_CHIP : I18nKey.SCIM$ACTIVE_CHIP,
            )}
          </span>
        </div>
        <span
          data-testid="scim-credential-details"
          className="text-xs leading-4 text-muted"
        >
          {details.join(" · ")}
        </span>
      </div>
      {!isRevoked && (
        <BrandButton
          testId="revoke-scim-credential"
          ariaLabel={t(I18nKey.SCIM$REVOKE_LABEL, { name: credential.name })}
          type="button"
          variant="danger"
          className="h-7 min-h-7 shrink-0 px-2.5 text-xs"
          onClick={onRevoke}
        >
          {t(I18nKey.SCIM$REVOKE)}
        </BrandButton>
      )}
    </li>
  );
}

/**
 * Org admin page: SCIM base URL and the credentials an identity provider uses.
 *
 * Keyed by the selected organization (below) so switching organizations
 * unmounts and remounts this: a fresh ``modal`` state (``null``) and fresh
 * mutation hooks, instead of carrying org A's open token/confirmation modal,
 * or a pending/just-settled mutation of org A's, onto org B's screen.
 */
function ScimSettingsContent() {
  const { t } = useTranslation();
  const setup = useScimSetup();
  const credentials = useScimCredentials();
  const createCredential = useCreateScimCredential();
  const revokeCredential = useRevokeScimCredential();

  const [modal, setModal] = React.useState<ModalState | null>(null);
  const closeModal = () => setModal(null);

  const providers: ScimProviderOption[] = setup.data?.providers ?? [];
  const canBind = setup.data?.can_bind_provider === true;
  const isLoading = setup.isLoading || credentials.isLoading;
  const hasError = !!setup.error || !!credentials.error;
  const items = credentials.data ?? [];

  const renderModal = () => {
    if (!modal) return null;
    if (modal.type === "token") {
      return <ScimTokenModal token={modal.token} onClose={closeModal} />;
    }
    if (modal.type === "revoke") {
      const { credential } = modal;
      const isLast = !canBind && isLastActiveForProvider(credential, items);
      return (
        <OrgModal
          testId="revoke-scim-credential-modal"
          title={t(I18nKey.SCIM$REVOKE_TITLE)}
          ariaLabel={t(I18nKey.SCIM$REVOKE_TITLE)}
          description={t(I18nKey.SCIM$REVOKE_WARNING, {
            name: credential.name,
          })}
          primaryButtonText={t(I18nKey.SCIM$REVOKE)}
          secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
          onPrimaryClick={() =>
            revokeCredential.mutate(
              { id: credential.id },
              { onSettled: closeModal },
            )
          }
          onClose={closeModal}
          isLoading={revokeCredential.isPending}
          primaryButtonTestId="confirm-button"
          secondaryButtonTestId="cancel-button"
          className="w-[calc(100vw-2rem)] max-w-md"
        >
          {isLast && (
            <p
              role="alert"
              data-testid="scim-revoke-last-warning"
              className="text-xs leading-5 text-[var(--oh-status-warning,#f5a524)]"
            >
              {t(I18nKey.SCIM$REVOKE_LAST_WARNING, {
                provider: credential.auth_provider_title ?? "",
              })}
            </p>
          )}
        </OrgModal>
      );
    }
    return (
      <CreateScimCredentialModal
        providers={providers}
        isLoading={createCredential.isPending}
        onClose={closeModal}
        onSubmit={(name, authProviderId) =>
          createCredential.mutate(
            { name, authProviderId },
            {
              onSuccess: ({ token }) => setModal({ type: "token", token }),
            },
          )
        }
      />
    );
  };

  return (
    <div data-testid="scim-settings" className="flex h-full flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <header className="min-w-0 space-y-1">
          <Typography.H2>{t(I18nKey.SETTINGS$NAV_SCIM)}</Typography.H2>
          <p
            data-testid="settings-page-subtitle"
            className="text-sm leading-5 text-muted"
          >
            {t(I18nKey.SETTINGS$PAGE_SCIM_SUBLINE)}
          </p>
        </header>
        <BrandButton
          testId="create-scim-credential-button"
          type="button"
          variant="primary"
          className="shrink-0 whitespace-nowrap"
          isDisabled={providers.length === 0}
          onClick={() => setModal({ type: "create" })}
          startContent={<Plus size={14} />}
        >
          {t(I18nKey.SCIM$CREATE)}
        </BrandButton>
      </div>

      {modal &&
        ReactDOM.createPortal(
          renderModal(),
          document.getElementById("portal-root") || document.body,
        )}

      {setup.data && (
        <section
          aria-labelledby="scim-connection-heading"
          className="flex flex-col gap-2"
        >
          <h3 id="scim-connection-heading" className="text-sm font-medium">
            {t(I18nKey.SCIM$CONNECTION_HEADER)}
          </h3>
          <div className="flex flex-wrap items-center gap-2">
            <code
              data-testid="scim-base-url"
              aria-label={t(I18nKey.SCIM$BASE_URL)}
              className="min-w-0 flex-1 break-all rounded-lg border border-[var(--oh-border)] bg-base-secondary px-3 py-2 font-mono text-sm"
            >
              {setup.data.base_url}
            </code>
            <BrandButton
              testId="copy-scim-base-url"
              ariaLabel={t(I18nKey.SCIM$COPY_BASE_URL)}
              type="button"
              variant="secondary"
              className="shrink-0"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(setup.data.base_url);
                  displaySuccessToast(t(I18nKey.SCIM$BASE_URL_COPIED));
                } catch {
                  displayErrorToast(t(I18nKey.SCIM$BASE_URL_COPY_FAILED));
                }
              }}
            >
              {t(I18nKey.BUTTON$COPY)}
            </BrandButton>
          </div>
          <p className="text-xs leading-5 text-muted">
            {t(I18nKey.SCIM$BASE_URL_HINT)}
          </p>
          {providers.length === 0 && <NoProvidersNotice canBind={canBind} />}
        </section>
      )}

      <div className={settingsListContainerClassName}>
        <div className={settingsListSectionHeaderClassName}>
          <span>{t(I18nKey.SCIM$CREDENTIALS_HEADER)}</span>
        </div>

        {isLoading && (
          <div className="flex items-center justify-center p-8 text-muted">
            {t(I18nKey.HOME$LOADING)}
          </div>
        )}

        {!isLoading && hasError && (
          <div
            role="alert"
            data-testid="scim-load-error"
            className="flex items-center justify-center p-8 text-muted"
          >
            {t(I18nKey.SCIM$LOAD_ERROR)}
          </div>
        )}

        {!isLoading && !hasError && items.length === 0 && (
          <div
            data-testid="scim-empty"
            className="flex items-center justify-center p-8 text-muted"
          >
            {t(I18nKey.SCIM$EMPTY)}
          </div>
        )}

        {!hasError && items.length > 0 && (
          <ul data-testid="scim-credentials-list">
            {items.map((credential) => (
              <CredentialRow
                key={credential.id}
                credential={credential}
                onRevoke={() => setModal({ type: "revoke", credential })}
              />
            ))}
          </ul>
        )}
      </div>

      <section aria-labelledby="scim-rotation-heading" className="space-y-1">
        <h3 id="scim-rotation-heading" className="text-sm font-medium">
          {t(I18nKey.SCIM$ROTATION_HEADER)}
        </h3>
        <p className="text-xs leading-5 text-muted">
          {t(I18nKey.SCIM$ROTATION_STEPS)}
        </p>
      </section>
    </div>
  );
}

/**
 * Remounts ``ScimSettingsContent`` on every organization switch (the ``key``
 * below), so none of its state -- the open modal, including a just-minted
 * token, or an in-flight/just-settled mutation -- survives onto the next
 * organization's screen.
 */
export default function ScimSettingsScreen() {
  const { organizationId } = useSelectedOrganizationId();
  return <ScimSettingsContent key={organizationId ?? "none"} />;
}
