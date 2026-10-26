import React from "react";
import ReactDOM from "react-dom";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { ServiceAccount } from "#/api/organization-service/service-accounts.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { ServiceAccountNameModal } from "#/components/features/service-accounts/service-account-name-modal";
import { ServiceAccountSecrets } from "#/components/features/service-accounts/service-account-secrets";
import { ServiceAccountBudget } from "#/components/features/service-accounts/service-account-budget";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useServiceAccounts } from "#/hooks/query/use-service-accounts";
import { useConfig } from "#/hooks/query/use-config";
import {
  useCreateServiceAccount,
  useDeleteServiceAccount,
  useDisableServiceAccount,
  useEnableServiceAccount,
  useRenameServiceAccount,
} from "#/hooks/mutation/use-service-account-mutations";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import { cn } from "#/utils/utils";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import {
  settingsListContainerClassName,
  settingsListRowClassName,
  settingsListSectionHeaderClassName,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";

export const clientLoader = createPermissionGuard("manage_service_accounts");

export const handle = { hideTitle: true };

/** The one modal open at a time, or none. */
type ModalState =
  | { type: "create" }
  | { type: "rename"; account: ServiceAccount }
  | { type: "delete"; account: ServiceAccount };

/** Org admin page: service accounts, with their secrets and monthly budget. */
export default function ServiceAccountsScreen() {
  const { t } = useTranslation();
  const { data: accounts, isLoading, error } = useServiceAccounts();
  const { data: config } = useConfig();
  const showBudget = config?.feature_flags?.enable_litellm !== false;

  const createAccount = useCreateServiceAccount();
  const renameAccount = useRenameServiceAccount();
  const disableAccount = useDisableServiceAccount();
  const enableAccount = useEnableServiceAccount();
  const deleteAccount = useDeleteServiceAccount();

  const [modal, setModal] = React.useState<ModalState | null>(null);
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const closeModal = () => setModal(null);

  const renderModal = () => {
    if (!modal) return null;
    if (modal.type === "delete") {
      return (
        <OrgModal
          title={t(I18nKey.SERVICE_ACCOUNTS$DELETE_TITLE)}
          description={t(I18nKey.SERVICE_ACCOUNTS$DELETE_WARNING, {
            name: modal.account.name,
          })}
          primaryButtonText={t(I18nKey.BUTTON$DELETE)}
          secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
          onPrimaryClick={() =>
            deleteAccount.mutate(
              { id: modal.account.id },
              { onSettled: closeModal },
            )
          }
          onClose={closeModal}
          isLoading={deleteAccount.isPending}
          primaryButtonTestId="confirm-button"
          secondaryButtonTestId="cancel-button"
        />
      );
    }
    if (modal.type === "rename") {
      return (
        <ServiceAccountNameModal
          currentName={modal.account.name}
          isLoading={renameAccount.isPending}
          onClose={closeModal}
          onSubmit={(name) =>
            renameAccount.mutate(
              { id: modal.account.id, name },
              { onSuccess: closeModal },
            )
          }
        />
      );
    }
    return (
      <ServiceAccountNameModal
        isLoading={createAccount.isPending}
        onClose={closeModal}
        onSubmit={(name) =>
          createAccount.mutate({ name }, { onSuccess: closeModal })
        }
      />
    );
  };

  return (
    <div
      data-testid="service-accounts-settings"
      className="flex h-full flex-col gap-6"
    >
      <div className="flex items-start justify-between gap-4">
        <header className="min-w-0 space-y-1">
          <Typography.H2>
            {t(I18nKey.SETTINGS$NAV_SERVICE_ACCOUNTS)}
          </Typography.H2>
          <p
            data-testid="settings-page-subtitle"
            className="text-sm leading-5 text-muted"
          >
            {t(I18nKey.SETTINGS$PAGE_SERVICE_ACCOUNTS_SUBLINE)}
          </p>
        </header>
        <BrandButton
          testId="create-service-account-button"
          type="button"
          variant="primary"
          className="shrink-0 whitespace-nowrap"
          onClick={() => setModal({ type: "create" })}
          startContent={<Plus size={14} />}
        >
          {t(I18nKey.SERVICE_ACCOUNTS$CREATE)}
        </BrandButton>
      </div>

      {modal &&
        ReactDOM.createPortal(
          renderModal(),
          document.getElementById("portal-root") || document.body,
        )}

      <div
        className={cn(
          settingsListContainerClassName,
          "custom-scrollbar flex-1 overflow-y-auto",
        )}
      >
        <div className={settingsListSectionHeaderClassName}>
          <span>{t(I18nKey.SERVICE_ACCOUNTS$LIST_HEADER)}</span>
        </div>

        {isLoading && (
          <div className="flex items-center justify-center p-8 text-muted">
            {t(I18nKey.HOME$LOADING)}
          </div>
        )}

        {!isLoading && error && (
          <div className="flex items-center justify-center p-8 text-muted">
            {t(I18nKey.SERVICE_ACCOUNTS$LOAD_ERROR)}
          </div>
        )}

        {!isLoading && !error && accounts?.length === 0 && (
          <div className="flex items-center justify-center p-8 text-muted">
            {t(I18nKey.SERVICE_ACCOUNTS$EMPTY)}
          </div>
        )}

        {!error && accounts && accounts.length > 0 && (
          <ul data-testid="service-accounts-list">
            {accounts.map((account) => {
              const isExpanded = expandedId === account.id;
              return (
                <li
                  key={account.id}
                  data-testid="service-account-item"
                  className={settingsListTableRowClassName}
                >
                  <div
                    className={cn(
                      settingsListRowClassName,
                      "justify-between gap-4",
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span
                        className={cn(
                          "truncate text-sm font-normal leading-5",
                          account.is_disabled ? "text-muted" : "text-white",
                        )}
                      >
                        {account.name}
                      </span>
                      {account.is_disabled && (
                        <span
                          data-testid="service-account-disabled-chip"
                          className="shrink-0 rounded-lg border border-[var(--oh-border)] px-2 py-0.5 text-xs text-muted"
                        >
                          {t(I18nKey.SERVICE_ACCOUNTS$DISABLED_CHIP)}
                        </span>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <BrandButton
                        testId="manage-service-account"
                        type="button"
                        variant="secondary"
                        className="h-7 min-h-7 px-2.5 text-xs"
                        onClick={() =>
                          setExpandedId(isExpanded ? null : account.id)
                        }
                      >
                        {t(I18nKey.SERVICE_ACCOUNTS$MANAGE)}
                      </BrandButton>
                      <BrandButton
                        testId="rename-service-account"
                        type="button"
                        variant="secondary"
                        className="h-7 min-h-7 px-2.5 text-xs"
                        onClick={() => setModal({ type: "rename", account })}
                      >
                        {t(I18nKey.BUTTON$RENAME)}
                      </BrandButton>
                      <BrandButton
                        testId="toggle-service-account"
                        type="button"
                        variant="secondary"
                        className="h-7 min-h-7 px-2.5 text-xs"
                        isDisabled={
                          disableAccount.isPending || enableAccount.isPending
                        }
                        onClick={() =>
                          account.is_disabled
                            ? enableAccount.mutate({ id: account.id })
                            : disableAccount.mutate({ id: account.id })
                        }
                      >
                        {t(
                          account.is_disabled
                            ? I18nKey.SERVICE_ACCOUNTS$ENABLE
                            : I18nKey.SERVICE_ACCOUNTS$DISABLE,
                        )}
                      </BrandButton>
                      <BrandButton
                        testId="delete-service-account"
                        type="button"
                        variant="danger"
                        className="h-7 min-h-7 px-2.5 text-xs"
                        onClick={() => setModal({ type: "delete", account })}
                      >
                        {t(I18nKey.BUTTON$DELETE)}
                      </BrandButton>
                    </div>
                  </div>
                  {isExpanded && (
                    <div
                      data-testid="service-account-details"
                      className="flex flex-col gap-6 border-t border-[var(--oh-border)] p-4"
                    >
                      <ServiceAccountSecrets saId={account.id} />
                      {showBudget && (
                        <ServiceAccountBudget
                          saId={account.id}
                          name={account.name}
                        />
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
