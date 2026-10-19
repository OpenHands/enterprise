import { useQueryClient } from "@tanstack/react-query";
import React, { useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  ORG_SECRETS_QUERY_KEY,
  useOrgSecrets,
  useSearchSecrets,
} from "#/hooks/query/use-get-secrets";
import { useDeleteSecret } from "#/hooks/mutation/use-delete-secret";
import { SecretForm } from "#/components/features/settings/secrets-settings/secret-form";
import {
  SecretListItem,
  SecretListItemSkeleton,
} from "#/components/features/settings/secrets-settings/secret-list-item";
import { BrandButton } from "#/components/features/settings/brand-button";
import { HorizontalScrollFade } from "#/components/shared/horizontal-scroll-fade";
import { ConfirmationModal } from "#/components/shared/modals/confirmation-modal";
import { I18nKey } from "#/i18n/declaration";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { Typography } from "#/ui/typography";
import { cn } from "#/utils/utils";
import {
  settingsListContainerClassName,
  settingsListScrollContainerClassName,
  settingsListScrollFadeFromClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
  settingsListTableMinWidthStyle,
} from "#/utils/settings-list-classes";
import { useMe } from "#/hooks/query/use-me";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { CustomSecretWithoutValue } from "#/api/secrets-service.types";

export const clientLoader = createPermissionGuard("manage_secrets");

export const handle = { hideTitle: true };

function SecretsTableHead() {
  const { t } = useTranslation();

  return (
    <thead className={settingsListTableHeadClassName}>
      <tr>
        <th className={cn(settingsListTableHeaderCellClassName, "w-1/4")}>
          {t(I18nKey.SETTINGS$NAME)}
        </th>
        <th className={cn(settingsListTableHeaderCellClassName, "w-1/2")}>
          {t(I18nKey.SECRETS$DESCRIPTION)}
        </th>
        <th
          className={cn(
            settingsListTableHeaderCellClassName,
            "w-1/4 text-right",
          )}
        >
          {t(I18nKey.SETTINGS$ACTIONS)}
        </th>
      </tr>
    </thead>
  );
}

function SecretsSettingsScreen() {
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { organizationId } = useSelectedOrganizationId();
  const { data: me } = useMe();
  const { hasPermission } = usePermission(me?.role ?? "member");
  const canManageOrgSecrets = hasPermission("manage_org_secrets");
  const tableContainerRef = useRef<HTMLDivElement>(null);

  const {
    data: secrets,
    isLoading: isLoadingSecrets,
    hasNextPage,
    isFetchingNextPage,
    onLoadMore,
  } = useSearchSecrets({ pageSize: 30 });
  const { data: orgSecrets } = useOrgSecrets();
  // Org-shared secrets are listed from the org endpoint below. The search
  // results interleave them after the personal pages, so leave them out here.
  const personalSecrets = secrets.filter(
    (secret) => secret.scope !== "organization",
  );

  const { mutate: deleteSecret } = useDeleteSecret();

  const [view, setView] = React.useState<
    "list" | "add-secret-form" | "edit-secret-form"
  >("list");
  const [selectedSecret, setSelectedSecret] = React.useState<string | null>(
    null,
  );
  const [selectedSecretIsShared, setSelectedSecretIsShared] =
    React.useState(false);
  const [selectedSecretDescription, setSelectedSecretDescription] =
    React.useState<string | undefined>(undefined);
  const [confirmationModalIsVisible, setConfirmationModalIsVisible] =
    React.useState(false);

  // Handle scroll for infinite loading
  const handleScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      const target = e.currentTarget;
      const isNearBottom =
        target.scrollHeight - target.scrollTop <= target.clientHeight + 100;

      if (isNearBottom && hasNextPage && !isFetchingNextPage) {
        onLoadMore();
      }
    },
    [hasNextPage, isFetchingNextPage, onLoadMore],
  );

  const invalidateSecrets = () => {
    // Invalidate both the new infinite query and the legacy query for compatibility
    queryClient.invalidateQueries({
      queryKey: ["secrets-search"],
    });
    queryClient.invalidateQueries({
      queryKey: ["secrets", organizationId],
    });
    queryClient.invalidateQueries({
      queryKey: [ORG_SECRETS_QUERY_KEY],
    });
  };

  const openEditForm = (
    secret: CustomSecretWithoutValue,
    isShared: boolean,
  ) => {
    setView("edit-secret-form");
    setSelectedSecret(secret.name);
    setSelectedSecretIsShared(isShared);
    setSelectedSecretDescription(secret.description);
  };

  const openDeleteConfirmation = (secretName: string, isShared: boolean) => {
    setConfirmationModalIsVisible(true);
    setSelectedSecret(secretName);
    setSelectedSecretIsShared(isShared);
  };

  const handleDeleteSecret = (secret: string, isShared: boolean) => {
    deleteSecret(
      { id: secret, isShared, organizationId },
      {
        onSettled: () => {
          setConfirmationModalIsVisible(false);
        },
        onSuccess: invalidateSecrets,
        onError: invalidateSecrets,
      },
    );
  };

  const onConfirmDeleteSecret = () => {
    if (selectedSecret) {
      handleDeleteSecret(selectedSecret, selectedSecretIsShared);
    }
  };

  const onCancelDeleteSecret = () => {
    setConfirmationModalIsVisible(false);
  };

  return (
    <div data-testid="secrets-settings-screen" className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <header className="min-w-0 space-y-1">
          <Typography.H2>{t(I18nKey.SETTINGS$NAV_SECRETS)}</Typography.H2>
          <p
            data-testid="settings-page-subtitle"
            className="text-sm leading-5 text-muted"
          >
            {t(I18nKey.SETTINGS$PAGE_SECRETS_SUBLINE)}
          </p>
        </header>
        {view === "list" ? (
          <BrandButton
            testId="add-secret-button"
            type="button"
            variant="primary"
            className="shrink-0 whitespace-nowrap"
            onClick={() => setView("add-secret-form")}
            isDisabled={isLoadingSecrets}
          >
            {t(I18nKey.SECRETS$ADD_NEW_SECRET)}
          </BrandButton>
        ) : null}
      </div>

      {isLoadingSecrets && view === "list" && (
        <ul>
          <SecretListItemSkeleton />
          <SecretListItemSkeleton />
          <SecretListItemSkeleton />
        </ul>
      )}

      {view === "list" && !isLoadingSecrets && (
        <div
          ref={tableContainerRef}
          className={cn(
            settingsListScrollContainerClassName,
            settingsListScrollFadeFromClassName,
          )}
          onScroll={handleScroll}
        >
          <HorizontalScrollFade>
            <table
              className="w-full table-fixed"
              style={settingsListTableMinWidthStyle(3)}
            >
              <SecretsTableHead />
              <tbody>
                {personalSecrets.map((secret) => (
                  <SecretListItem
                    key={secret.name}
                    title={secret.name}
                    description={secret.description}
                    onEdit={() => openEditForm(secret, false)}
                    onDelete={() => openDeleteConfirmation(secret.name, false)}
                  />
                ))}
              </tbody>
            </table>
          </HorizontalScrollFade>

          {/* Loading indicator for infinite scroll */}
          {isFetchingNextPage && (
            <div className="flex justify-center p-4">
              <LoadingSpinner size="small" />
            </div>
          )}
        </div>
      )}

      {view === "list" && !isLoadingSecrets && orgSecrets.length > 0 && (
        <section
          data-testid="org-secrets-section"
          className="mt-2 flex flex-col gap-4 border-t border-[var(--oh-border)] pt-6"
        >
          <header className="min-w-0 space-y-1">
            <Typography.H2>
              {t(I18nKey.SECRETS$ORG_SECRETS_TITLE)}
            </Typography.H2>
            <p className="text-sm leading-5 text-muted">
              {t(I18nKey.SECRETS$SHARE_WITH_ORGANIZATION_DESCRIPTION)}
            </p>
          </header>
          <div
            className={cn(
              settingsListContainerClassName,
              settingsListScrollFadeFromClassName,
            )}
          >
            <HorizontalScrollFade testId="org-secrets-table-scroll">
              <table
                className="w-full table-fixed"
                style={settingsListTableMinWidthStyle(3)}
              >
                <SecretsTableHead />
                <tbody>
                  {orgSecrets.map((secret) => (
                    <SecretListItem
                      key={secret.name}
                      title={secret.name}
                      description={secret.description}
                      canEdit={canManageOrgSecrets}
                      onEdit={() => openEditForm(secret, true)}
                      onDelete={() => openDeleteConfirmation(secret.name, true)}
                    />
                  ))}
                </tbody>
              </table>
            </HorizontalScrollFade>
          </div>
        </section>
      )}

      {(view === "add-secret-form" || view === "edit-secret-form") && (
        <SecretForm
          mode={view === "add-secret-form" ? "add" : "edit"}
          selectedSecret={selectedSecret}
          isSharedSecret={selectedSecretIsShared}
          initialDescription={selectedSecretDescription}
          onCancel={() => setView("list")}
        />
      )}

      {confirmationModalIsVisible && (
        <ConfirmationModal
          text={t(I18nKey.SECRETS$CONFIRM_DELETE_KEY)}
          onConfirm={onConfirmDeleteSecret}
          onCancel={onCancelDeleteSecret}
        />
      )}
    </div>
  );
}

export default SecretsSettingsScreen;
