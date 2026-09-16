import { useMemo, useState } from "react";
import { ChevronRight, MessageSquare } from "lucide-react";
import { useTranslation } from "react-i18next";
import { AddCustomMcpModal } from "#/components/features/integrations-hub/add-custom-mcp-modal";
import { ConnectorSetupProgress } from "#/components/features/integrations-hub/connector-setup-progress";
import { HubBadge } from "#/components/features/integrations-hub/hub-badge";
import { HubCirclePlusCheckToggle } from "#/components/features/integrations-hub/hub-circle-plus-check-toggle";
import { HubCountBadge } from "#/components/features/integrations-hub/hub-count-badge";
import { HubFilterTabs } from "#/components/features/integrations-hub/hub-filter-tabs";
import { HubHoverCard } from "#/components/features/integrations-hub/hub-hover-card";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { HubSearchField } from "#/components/features/integrations-hub/hub-search-field";
import {
  HubCatalogViewToggle,
  type HubCatalogView,
} from "#/components/features/integrations-hub/hub-catalog-view-toggle";
import {
  hubCardGridClassName,
  hubCardPillClassName,
  hubSearchEmptyStateClassName,
} from "#/components/features/integrations-hub/hub-card-classes";
import { HubIntegrationCard } from "#/components/features/integrations-hub/hub-integration-card";
import { HubIntegrationEnableRow } from "#/components/features/integrations-hub/integration-detail-modal";
import {
  formatHubTimestamp,
  hubAuthLabel,
  hubIntegrationFromUserRequest,
} from "#/components/features/integrations-hub/hub-format";
import {
  HubModal,
  hubModalBodyClassName,
  hubModalFooterClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { IntegrationsHubPageHeader } from "#/components/features/integrations-hub/integrations-hub-page-header";
import { BrandButton } from "#/components/features/settings/brand-button";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import { ConfirmationModal } from "#/components/shared/modals/confirmation-modal";
import { useIntegrationsHubStub } from "#/hooks/query/use-integrations-hub-stub";
import { I18nKey } from "#/i18n/declaration";
import type {
  HubDuplicateGroup,
  HubIntegration,
  HubOverviewConnection,
  HubOverviewUser,
  HubToolAccessMode,
  HubUserRequest,
} from "#/types/integrations-hub";
import { formControlTransitionClassName } from "#/utils/form-control-classes";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
  settingsListIconActionButtonClassName,
  settingsListRowClassName,
  settingsListRowHoverClassName,
  settingsListSectionHeaderClassName,
  settingsListTableCellClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

function CatalogConnectorModal({
  integration,
  onClose,
  onRegister,
  onDelete,
  onToggleEnabled,
  onToolAccessModeChange,
}: {
  integration: HubIntegration;
  onClose: () => void;
  onRegister: () => void;
  onDelete: () => void;
  onToggleEnabled: () => void;
  onToolAccessModeChange: (toolName: string, mode: HubToolAccessMode) => void;
}) {
  const { t } = useTranslation();

  return (
    <HubModal
      ariaLabel={t(I18nKey.INTEGRATIONS_HUB$CONNECTOR_DETAILS, {
        name: integration.name,
      })}
      testId={`connector-details-modal-${integration.slug}`}
      width="xl"
      className="min-h-0 max-h-[90vh] gap-0"
      onClose={onClose}
    >
      <div className="shrink-0 border-b border-[var(--oh-border)] px-7 pb-4 pr-12 pt-7">
        <div className="flex items-start gap-3">
          <IntegrationProviderIcon
            provider={integration.slug}
            logoUrl={integration.logoUrl}
            size="md"
          />
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-white">
              {integration.name}
            </h2>
            {integration.description ? (
              <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
                {integration.description}
              </p>
            ) : null}
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {(integration.categories?.length
                ? integration.categories
                : [integration.kind]
              )
                .filter(Boolean)
                .map((category) => (
                  <span
                    key={`${integration.slug}-${category}`}
                    className={hubCardPillClassName}
                  >
                    {category}
                  </span>
                ))}
            </div>
          </div>
        </div>
        {integration.connected ? (
          <HubIntegrationEnableRow
            integration={integration}
            onToggleEnabled={onToggleEnabled}
          />
        ) : null}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-4">
        <ConnectorSetupProgress
          integration={integration}
          isRegistered={integration.connected}
          onRegister={onRegister}
          onDelete={onDelete}
          onToolAccessModeChange={onToolAccessModeChange}
        />
      </div>
      <div className={hubModalFooterClassName}>
        <BrandButton type="button" variant="secondary" onClick={onClose}>
          {t(I18nKey.INTEGRATIONS_HUB$CLOSE)}
        </BrandButton>
      </div>
    </HubModal>
  );
}

function hasUserRequestDetails(request: HubUserRequest) {
  return Boolean(
    request.notes?.trim() ||
    request.description?.trim() ||
    request.docsUrl?.trim(),
  );
}

function UserRequestNotesContent({ request }: { request: HubUserRequest }) {
  const { t } = useTranslation();
  const showFormFields = Boolean(
    request.source === "custom" ||
    request.description?.trim() ||
    request.docsUrl?.trim(),
  );

  if (!showFormFields) {
    return request.notes;
  }

  return (
    <div className="space-y-2">
      {request.description?.trim() ? (
        <div>
          <p className="text-[10px] font-medium leading-4 text-[var(--oh-text-secondary)]">
            {t(I18nKey.INTEGRATIONS_HUB$REQUEST_CUSTOM_DESCRIPTION)}
          </p>
          <p>{request.description}</p>
        </div>
      ) : null}
      {request.docsUrl?.trim() ? (
        <div>
          <p className="text-[10px] font-medium leading-4 text-[var(--oh-text-secondary)]">
            {t(I18nKey.INTEGRATIONS_HUB$REQUEST_DOCS_URL)}
          </p>
          <p className="break-all">{request.docsUrl}</p>
        </div>
      ) : null}
      {request.notes?.trim() ? (
        <div>
          <p className="text-[10px] font-medium leading-4 text-[var(--oh-text-secondary)]">
            {t(I18nKey.INTEGRATIONS_HUB$REQUEST_NOTES)}
          </p>
          <p>{request.notes}</p>
        </div>
      ) : null}
    </div>
  );
}

function TimestampCell({ value }: { value?: string }) {
  const parts = formatHubTimestamp(value);
  if (!parts) {
    return <span className="text-[10px] leading-4 text-white">—</span>;
  }
  return (
    <div className="flex flex-col text-[10px] leading-4 text-white">
      <span>{parts.date}</span>
      <span className="text-[var(--oh-text-secondary)]">{parts.time}</span>
    </div>
  );
}

function MetricTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-[var(--oh-border)] bg-base-secondary px-3 py-2">
      <p className="text-xs text-tertiary-light">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-white">
        {value}
      </p>
    </div>
  );
}

function ConnectionRow({ connection }: { connection: HubOverviewConnection }) {
  const { t } = useTranslation();
  const identity =
    connection.displayName.trim() ||
    connection.externalAccountId.trim() ||
    t(I18nKey.INTEGRATIONS_HUB$CONNECTION_IDENTITY_FALLBACK);

  return (
    <div
      data-testid="admin-overview-connection-row"
      className="grid gap-2 px-3 py-3 text-sm sm:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,1.2fr)_auto] sm:items-center sm:gap-3"
    >
      <div className="min-w-0">
        <p className="font-medium text-white">{connection.integrationKey}</p>
        <p className="text-xs text-tertiary-alt">{connection.provider}</p>
      </div>
      <HubTruncatedText text={identity} className="text-tertiary-light" />
      <HubTruncatedText
        text={
          connection.externalAccountId ||
          t(I18nKey.INTEGRATIONS_HUB$NO_EXTERNAL_ACCOUNT)
        }
        className="text-xs text-tertiary-alt"
      />
      <div className="flex flex-wrap items-center gap-2">
        <HubBadge>{hubAuthLabel(connection.authStrategy, t)}</HubBadge>
        <span className="text-xs text-tertiary-alt">
          {formatHubTimestamp(connection.updatedAt)?.date}
        </span>
      </div>
    </div>
  );
}

function DuplicateGroupCard({ group }: { group: HubDuplicateGroup }) {
  const { t } = useTranslation();
  return (
    <div
      data-testid="admin-overview-duplicate-group-row"
      className="rounded-xl border border-[var(--oh-border)] bg-base-secondary px-3 py-3"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-white">{group.provider}</span>
        <HubBadge>{group.externalAccountId}</HubBadge>
        <HubBadge>
          {t(I18nKey.INTEGRATIONS_HUB$DUPLICATE_OWNERS, {
            count: group.ownerIds.length,
          })}
        </HubBadge>
      </div>
      {group.displayNames && group.displayNames.length > 0 ? (
        <p className="mt-2 text-xs text-tertiary-light">
          {group.displayNames.join(", ")}
        </p>
      ) : null}
      <p className="mt-2 text-xs text-tertiary-alt">
        {t(I18nKey.INTEGRATIONS_HUB$DUPLICATE_OWNERS_LABEL)}:{" "}
        {group.ownerIds.join(", ")}
      </p>
    </div>
  );
}

function DuplicateGroupModal({
  group,
  onClose,
  onSelectOwner,
}: {
  group: HubDuplicateGroup;
  onClose: () => void;
  onSelectOwner: (ownerId: string) => void;
}) {
  const { t } = useTranslation();

  return (
    <HubModal
      ariaLabel={t(I18nKey.INTEGRATIONS_HUB$DUPLICATE_GROUP_ARIA, {
        provider: group.provider,
        accountId: group.externalAccountId,
      })}
      testId="admin-overview-duplicate-group-modal"
      className="min-h-0 max-h-[90vh] gap-0"
      onClose={onClose}
    >
      <header className="shrink-0 border-b border-[var(--oh-border)] px-7 pb-4 pr-12 pt-7">
        <div className="flex items-center gap-3">
          <IntegrationProviderIcon
            provider={group.provider.toLowerCase()}
            size="md"
          />
          <div className="min-w-0 flex-1 leading-none">
            <h2 className="text-base font-semibold leading-5 text-white">
              {group.provider}
            </h2>
            <p className="text-xs leading-4 text-tertiary-light">
              {group.externalAccountId}
            </p>
          </div>
        </div>
      </header>
      <div className="custom-scrollbar-always min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-5">
        <h3 className="flex items-center text-sm font-semibold text-white">
          {t(I18nKey.INTEGRATIONS_HUB$DUPLICATE_OWNERS_LABEL)}
          <HubCountBadge count={group.ownerIds.length} />
        </h3>
        <div className="mt-3 divide-y divide-[var(--oh-border)] overflow-hidden rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)]">
          {group.ownerIds.map((ownerId) => (
            <button
              key={ownerId}
              type="button"
              data-testid={`admin-overview-duplicate-owner-${ownerId}`}
              onClick={() => onSelectOwner(ownerId)}
              className="flex w-full cursor-pointer items-center justify-between gap-3 px-4 py-3 text-left text-sm text-white transition-colors hover:bg-[var(--oh-interactive-hover)]"
            >
              <HubTruncatedText text={ownerId} />
              <ChevronRight className="h-4 w-4 shrink-0 text-tertiary-light" />
            </button>
          ))}
        </div>
      </div>
      <div className={hubModalFooterClassName}>
        <BrandButton type="button" variant="secondary" onClick={onClose}>
          {t(I18nKey.INTEGRATIONS_HUB$CLOSE)}
        </BrandButton>
      </div>
    </HubModal>
  );
}

function AdminOverviewUserDetails({ user }: { user: HubOverviewUser }) {
  const { t } = useTranslation();

  return (
    <div data-testid="admin-overview-user-details" className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <MetricTile
          label={t(I18nKey.INTEGRATIONS_HUB$TOTAL_ACCESS_REQUESTS)}
          value={user.totalAccessRequests}
        />
        <MetricTile
          label={t(I18nKey.INTEGRATIONS_HUB$PENDING_APPROVALS)}
          value={user.pendingAccessRequests}
        />
        <MetricTile
          label={t(I18nKey.INTEGRATIONS_HUB$NOTIFICATIONS)}
          value={user.notifications}
        />
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium text-white">
          {t(I18nKey.INTEGRATIONS_HUB$INTEGRATIONS_LABEL)}
        </h3>
        {user.integrations.length === 0 ? (
          <p className="text-sm text-tertiary-alt">
            {t(I18nKey.INTEGRATIONS_HUB$NO_INTEGRATIONS)}
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {user.integrations.map((integration) => (
              <HubBadge key={integration}>{integration}</HubBadge>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium text-white">
          {t(I18nKey.INTEGRATIONS_HUB$PROVIDERS_LABEL)}
        </h3>
        {user.providers.length === 0 ? (
          <p className="text-sm text-tertiary-alt">
            {t(I18nKey.INTEGRATIONS_HUB$NO_PROVIDERS)}
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {user.providers.map((provider) => (
              <HubBadge key={provider}>{provider}</HubBadge>
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium text-white">
          {t(I18nKey.INTEGRATIONS_HUB$SAVED_IDENTITIES, {
            count: user.totalConnections,
          })}
        </h3>
        {user.connections.length === 0 ? (
          <p className="text-sm text-tertiary-alt">
            {t(I18nKey.INTEGRATIONS_HUB$NO_CONNECTIONS_USER)}
          </p>
        ) : (
          <div
            className={cn(
              settingsListContainerClassName,
              "divide-y divide-[var(--oh-border)]",
            )}
          >
            {user.connections.map((connection) => (
              <ConnectionRow
                key={`${connection.integrationKey}-${connection.provider}-${connection.updatedAt}`}
                connection={connection}
              />
            ))}
          </div>
        )}
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-medium text-white">
          {t(I18nKey.INTEGRATIONS_HUB$DUPLICATE_ACCOUNTS)}
        </h3>
        {user.duplicateExternalAccounts.length === 0 ? (
          <p className="text-sm text-tertiary-alt">
            {t(I18nKey.INTEGRATIONS_HUB$DUPLICATE_ACCOUNTS_EMPTY)}
          </p>
        ) : (
          <div className="space-y-2">
            {user.duplicateExternalAccounts.map((group) => (
              <DuplicateGroupCard
                key={`${group.provider}-${group.externalAccountId}`}
                group={group}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function OverviewUsersList({
  users,
  onSelect,
}: {
  users: HubOverviewUser[];
  onSelect: (user: HubOverviewUser) => void;
}) {
  const { t } = useTranslation();

  if (users.length === 0) {
    return (
      <div className={hubSearchEmptyStateClassName}>
        <p className="text-xs text-tertiary-light">
          {t(I18nKey.INTEGRATIONS_HUB$NO_USERS)}
        </p>
      </div>
    );
  }

  return (
    <div className={settingsListContainerClassName}>
      <div className={settingsListSectionHeaderClassName}>
        <span>{t(I18nKey.INTEGRATIONS_HUB$OVERVIEW_TAB_USERS)}</span>
      </div>
      <ul>
        {users.map((user) => (
          <li key={user.ownerId} className={settingsListTableRowClassName}>
            <button
              type="button"
              data-testid="admin-overview-user-row"
              className={cn(
                settingsListRowClassName,
                "w-full justify-between gap-3 text-left",
              )}
              onClick={() => onSelect(user)}
            >
              <HubTruncatedText
                text={user.ownerId}
                className="text-sm font-normal leading-5 text-white"
              />
              <span className="flex min-w-0 shrink-0 items-center gap-2">
                <HubTruncatedText
                  text={[
                    t(I18nKey.INTEGRATIONS_HUB$CONNECTIONS, {
                      count: user.totalConnections,
                    }),
                    t(I18nKey.INTEGRATIONS_HUB$PENDING_LABEL, {
                      count: user.pendingAccessRequests,
                    }),
                    t(I18nKey.INTEGRATIONS_HUB$NOTIFICATIONS_INLINE, {
                      count: user.notifications,
                    }),
                  ].join(" · ")}
                  className="max-w-64 text-xs font-normal leading-4 text-muted"
                />
                <ChevronRight
                  data-testid="admin-overview-user-row-caret"
                  className="h-4 w-4 shrink-0 text-muted"
                />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function OverviewDuplicatesList({
  duplicates,
  onSelect,
}: {
  duplicates: HubDuplicateGroup[];
  onSelect: (group: HubDuplicateGroup) => void;
}) {
  const { t } = useTranslation();

  if (duplicates.length === 0) {
    return (
      <div className={hubSearchEmptyStateClassName}>
        <p className="text-xs text-tertiary-light">
          {t(I18nKey.INTEGRATIONS_HUB$NO_DUPLICATES)}
        </p>
      </div>
    );
  }

  return (
    <div className={settingsListContainerClassName}>
      <div className={settingsListSectionHeaderClassName}>
        <span>{t(I18nKey.INTEGRATIONS_HUB$OVERVIEW_TAB_DUPLICATES)}</span>
      </div>
      <ul>
        {duplicates.map((group) => (
          <li
            key={`${group.provider}-${group.externalAccountId}`}
            className={settingsListTableRowClassName}
          >
            <button
              type="button"
              data-testid="admin-overview-duplicate-row"
              className={cn(
                settingsListRowClassName,
                "w-full justify-between gap-3 text-left",
              )}
              onClick={() => onSelect(group)}
            >
              <span className="flex min-w-0 items-center gap-2">
                <IntegrationProviderIcon
                  provider={group.provider.toLowerCase()}
                  size="sm"
                />
                <HubTruncatedText
                  text={group.provider}
                  className="text-sm font-normal leading-5 text-white"
                />
              </span>
              <span className="flex min-w-0 shrink-0 items-center gap-2">
                <HubTruncatedText
                  text={[
                    group.externalAccountId,
                    group.ownerIds.join(", "),
                  ].join(" · ")}
                  className="max-w-64 text-xs font-normal leading-4 text-muted"
                />
                <ChevronRight className="h-4 w-4 shrink-0 text-muted" />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function CatalogListRow({
  integration,
  isSelected,
  onSelect,
  onToggle,
}: {
  integration: HubIntegration;
  isSelected: boolean;
  onSelect: () => void;
  onToggle?: () => void;
}) {
  const { t } = useTranslation();
  const authLabel = hubAuthLabel(integration.authStrategy, t);
  const isInstalled = integration.connected;
  return (
    <div
      role="button"
      tabIndex={0}
      data-testid={`admin-catalog-row-${integration.slug}`}
      aria-haspopup="dialog"
      aria-expanded={isSelected}
      className={cn(
        "flex w-full min-w-0 cursor-pointer items-center gap-3 px-4 py-3 text-left text-sm",
        formControlTransitionClassName,
        settingsListRowHoverClassName,
      )}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      <IntegrationProviderIcon
        provider={integration.slug}
        logoUrl={integration.logoUrl}
        size="sm"
      />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2 overflow-x-auto whitespace-nowrap">
          <span className="shrink-0 font-medium text-white">
            {integration.name}
          </span>
          {authLabel ? (
            <span className="shrink-0 text-xs text-tertiary-alt">
              {authLabel}
            </span>
          ) : null}
          {integration.connected && integration.toolCount > 0 ? (
            <HubBadge size="sm">
              {t(I18nKey.INTEGRATIONS_HUB$TOOLS_COUNT, {
                count: integration.tools.length || integration.toolCount,
              })}
            </HubBadge>
          ) : null}
        </div>
        {integration.description ? (
          <HubTruncatedText
            text={integration.description}
            className="mt-0.5 text-xs text-[var(--oh-text-secondary)]"
          />
        ) : null}
      </div>
      <HubCirclePlusCheckToggle
        testId={
          isInstalled
            ? `admin-catalog-toggle-${integration.slug}`
            : `admin-catalog-connect-${integration.slug}`
        }
        isSelected={isInstalled && integration.enabled}
        onToggle={(selected) => {
          if (isInstalled) {
            onToggle?.();
            return;
          }
          if (selected) {
            onSelect();
          }
        }}
        enableLabel={t(
          isInstalled
            ? I18nKey.INTEGRATIONS_HUB$ENABLE
            : I18nKey.INTEGRATIONS_HUB$CONNECT,
        )}
        disableLabel={t(
          isInstalled
            ? I18nKey.INTEGRATIONS_HUB$DISABLE
            : I18nKey.INTEGRATIONS_HUB$CONNECT,
        )}
        enableTooltip={t(
          isInstalled
            ? I18nKey.INTEGRATIONS_HUB$ENABLE
            : I18nKey.INTEGRATIONS_HUB$CONNECT,
        )}
        disableTooltip={t(I18nKey.INTEGRATIONS_HUB$DISABLE)}
        removeTooltip={t(I18nKey.INTEGRATIONS_HUB$DISABLE)}
      />
    </div>
  );
}

function CatalogSection({
  title,
  count,
  testId,
  items,
  view,
  selectedSlug,
  emptyLabel,
  onSelect,
  onToggle,
}: {
  title: string;
  count: number;
  testId: string;
  items: HubIntegration[];
  view: HubCatalogView;
  selectedSlug: string | null;
  emptyLabel: string;
  onSelect: (slug: string) => void;
  onToggle?: (slug: string) => void;
}) {
  let body = (
    <div
      data-testid={`${testId}-list`}
      className={cn(
        settingsListContainerClassName,
        settingsListDividerClassName,
        "min-w-0",
      )}
    >
      {items.map((integration) => (
        <CatalogListRow
          key={integration.slug}
          integration={integration}
          isSelected={selectedSlug === integration.slug}
          onSelect={() => onSelect(integration.slug)}
          onToggle={() => onToggle?.(integration.slug)}
        />
      ))}
    </div>
  );
  if (items.length === 0) {
    body = (
      <div className={hubSearchEmptyStateClassName}>
        <p className="text-xs text-tertiary-light">{emptyLabel}</p>
      </div>
    );
  } else if (view === "card") {
    body = (
      <div className="@container min-w-0 w-full">
        <div data-testid={`${testId}-grid`} className={hubCardGridClassName}>
          {items.map((integration) => (
            <HubIntegrationCard
              key={integration.slug}
              integration={integration}
              isInstalled={integration.connected}
              testId={`admin-catalog-row-${integration.slug}`}
              toggleTestId={
                integration.connected
                  ? `admin-catalog-toggle-${integration.slug}`
                  : `admin-catalog-connect-${integration.slug}`
              }
              ariaHasPopup="dialog"
              ariaExpanded={selectedSlug === integration.slug}
              onOpen={() => onSelect(integration.slug)}
              onToggle={() => onToggle?.(integration.slug)}
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <section data-testid={testId} className="flex flex-col gap-3">
      <div className="flex items-center">
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
        <HubCountBadge count={count} />
      </div>
      {body}
    </section>
  );
}

export function AdminOverviewPage() {
  const { t } = useTranslation();
  const { overviewUsers, duplicateGroups } = useIntegrationsHubStub();
  const [tab, setTab] = useState<"users" | "duplicates">("users");
  const [search, setSearch] = useState("");
  const [selectedUser, setSelectedUser] = useState<HubOverviewUser | null>(
    null,
  );
  const [selectedDuplicate, setSelectedDuplicate] =
    useState<HubDuplicateGroup | null>(null);

  const query = search.trim().toLowerCase();
  const users = overviewUsers.filter(
    (user) =>
      !query ||
      `${user.ownerId} ${user.integrations.join(" ")}`
        .toLowerCase()
        .includes(query),
  );
  const duplicates = duplicateGroups.filter(
    (group) =>
      !query ||
      `${group.provider} ${group.externalAccountId} ${group.ownerIds.join(" ")}`
        .toLowerCase()
        .includes(query),
  );

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="integrations-hub-admin-overview"
    >
      <IntegrationsHubPageHeader
        title={I18nKey.INTEGRATIONS_HUB$NAV_ADMIN_OVERVIEW}
        subtitle={I18nKey.INTEGRATIONS_HUB$PAGE_ADMIN_OVERVIEW_SUBLINE}
      />
      <HubFilterTabs
        testId="overview-tabs"
        ariaLabel={t(I18nKey.INTEGRATIONS_HUB$OVERVIEW_TABS_ARIA)}
        value={tab}
        onChange={setTab}
        options={[
          {
            value: "users",
            label: t(I18nKey.INTEGRATIONS_HUB$OVERVIEW_TAB_USERS),
            count: overviewUsers.length,
          },
          {
            value: "duplicates",
            label: t(I18nKey.INTEGRATIONS_HUB$OVERVIEW_TAB_DUPLICATES),
            count: duplicateGroups.length,
          },
        ]}
      />
      <p className="text-sm leading-5 text-tertiary-light">
        {tab === "users"
          ? t(I18nKey.INTEGRATIONS_HUB$OVERVIEW_USERS_HINT)
          : t(I18nKey.INTEGRATIONS_HUB$OVERVIEW_DUPLICATES_HINT)}
      </p>
      <HubSearchField
        value={search}
        onChange={setSearch}
        placeholder={t(
          tab === "users"
            ? I18nKey.INTEGRATIONS_HUB$SEARCH_USERS
            : I18nKey.INTEGRATIONS_HUB$SEARCH_DUPLICATES,
        )}
        testId="integrations-hub-overview-search"
      />

      {tab === "users" ? (
        <OverviewUsersList users={users} onSelect={setSelectedUser} />
      ) : (
        <OverviewDuplicatesList
          duplicates={duplicates}
          onSelect={setSelectedDuplicate}
        />
      )}

      {selectedUser ? (
        <HubModal
          ariaLabel={t(I18nKey.INTEGRATIONS_HUB$USER_DETAIL_ARIA, {
            ownerId: selectedUser.ownerId,
          })}
          testId="admin-overview-user-modal"
          width="xl"
          onClose={() => setSelectedUser(null)}
        >
          <div className={hubModalBodyClassName}>
            <h2 className="pr-8 text-base font-semibold text-white">
              {selectedUser.ownerId}
            </h2>
            <div className="mt-5">
              <AdminOverviewUserDetails user={selectedUser} />
            </div>
          </div>
          <div className={hubModalFooterClassName}>
            <BrandButton
              type="button"
              variant="secondary"
              onClick={() => setSelectedUser(null)}
            >
              {t(I18nKey.INTEGRATIONS_HUB$CLOSE)}
            </BrandButton>
          </div>
        </HubModal>
      ) : null}

      {selectedDuplicate && !selectedUser ? (
        <DuplicateGroupModal
          group={selectedDuplicate}
          onClose={() => setSelectedDuplicate(null)}
          onSelectOwner={(ownerId) => {
            const owner =
              overviewUsers.find((user) => user.ownerId === ownerId) ?? null;
            setSelectedDuplicate(null);
            setSelectedUser(owner);
          }}
        />
      ) : null}
    </div>
  );
}

export function AdminCatalogPage() {
  const { t } = useTranslation();
  const {
    catalogIntegrations,
    connect,
    disconnect,
    toggleEnabled,
    registerCustomMcp,
    updateToolAccess,
  } = useIntegrationsHubStub();
  const [search, setSearch] = useState("");
  const [view, setView] = useState<HubCatalogView>("card");
  const [customOpen, setCustomOpen] = useState(false);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  const query = search.trim().toLowerCase();
  const visible = catalogIntegrations.filter(
    (item) =>
      !query ||
      `${item.name} ${item.description} ${item.kind} ${item.slug}`
        .toLowerCase()
        .includes(query),
  );
  const registered = visible.filter((item) => item.connected);
  const available = visible.filter((item) => !item.connected);
  const selected =
    catalogIntegrations.find((item) => item.slug === selectedSlug) ?? null;

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="integrations-hub-admin-catalog"
    >
      <IntegrationsHubPageHeader
        title={I18nKey.INTEGRATIONS_HUB$NAV_ADMIN_CATALOG}
        subtitle={I18nKey.INTEGRATIONS_HUB$PAGE_ADMIN_CATALOG_SUBLINE}
        actions={
          <BrandButton
            type="button"
            variant="primary"
            onClick={() => setCustomOpen(true)}
          >
            {t(I18nKey.INTEGRATIONS_HUB$ADD_MCP_SERVER)}
          </BrandButton>
        }
      />
      <div className="flex items-stretch gap-2">
        <HubSearchField
          value={search}
          onChange={setSearch}
          placeholder={t(I18nKey.INTEGRATIONS_HUB$SEARCH_CATALOG)}
          testId="integrations-hub-catalog-search"
        />
        <HubCatalogViewToggle view={view} onChange={setView} />
      </div>
      <CatalogSection
        title={t(I18nKey.INTEGRATIONS_HUB$REGISTERED)}
        count={registered.length}
        testId="admin-catalog-registered"
        items={registered}
        view={view}
        selectedSlug={selectedSlug}
        emptyLabel={
          query
            ? t(I18nKey.INTEGRATIONS_HUB$EMPTY)
            : t(I18nKey.INTEGRATIONS_HUB$REGISTERED_EMPTY)
        }
        onSelect={setSelectedSlug}
        onToggle={toggleEnabled}
      />
      <CatalogSection
        title={t(I18nKey.INTEGRATIONS_HUB$AVAILABLE_INTEGRATIONS)}
        count={available.length}
        testId="admin-catalog-available"
        items={available}
        view={view}
        selectedSlug={selectedSlug}
        emptyLabel={t(I18nKey.INTEGRATIONS_HUB$EMPTY)}
        onSelect={setSelectedSlug}
      />

      {selected ? (
        <CatalogConnectorModal
          integration={selected}
          onClose={() => setSelectedSlug(null)}
          onRegister={() => connect(selected.slug)}
          onDelete={() => {
            disconnect(selected.slug);
            setSelectedSlug(null);
          }}
          onToggleEnabled={() => toggleEnabled(selected.slug)}
          onToolAccessModeChange={(toolName, mode) =>
            updateToolAccess(selected.slug, toolName, mode)
          }
        />
      ) : null}

      {customOpen ? (
        <AddCustomMcpModal
          existingSlugs={catalogIntegrations.map((item) => item.slug)}
          onClose={() => setCustomOpen(false)}
          onRegister={(input) => {
            registerCustomMcp(input);
            setCustomOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}

const userRequestHeaderCellClassName = cn(
  settingsListTableHeaderCellClassName,
  "px-3",
);

export function AdminUserRequestsPage() {
  const { t } = useTranslation();
  const {
    userRequests,
    catalogIntegrations,
    connect,
    disconnect,
    toggleEnabled,
    updateToolAccess,
    dismissUserRequest,
  } = useIntegrationsHubStub();
  const [setupRequestId, setSetupRequestId] = useState<string | null>(null);
  const [setupSeed, setSetupSeed] = useState<HubIntegration | null>(null);
  const [pendingDismissId, setPendingDismissId] = useState<string | null>(null);

  const pendingDismissRequest = useMemo(
    () => userRequests.find((item) => item.id === pendingDismissId) ?? null,
    [pendingDismissId, userRequests],
  );

  const selected = useMemo(() => {
    if (!setupSeed) {
      return null;
    }
    return (
      catalogIntegrations.find((item) => item.slug === setupSeed.slug) ??
      setupSeed
    );
  }, [catalogIntegrations, setupSeed]);

  const closeSetup = () => {
    setSetupRequestId(null);
    setSetupSeed(null);
  };

  const openSetup = (request: HubUserRequest) => {
    setSetupRequestId(request.id);
    setSetupSeed(
      catalogIntegrations.find((item) => item.slug === request.slug) ??
        hubIntegrationFromUserRequest(request),
    );
  };

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="integrations-hub-admin-user-requests"
    >
      <IntegrationsHubPageHeader
        title={I18nKey.INTEGRATIONS_HUB$NAV_ADMIN_USER_REQUESTS}
        subtitle={I18nKey.INTEGRATIONS_HUB$PAGE_ADMIN_USER_REQUESTS_SUBLINE}
      />
      {userRequests.length === 0 ? (
        <div className={hubSearchEmptyStateClassName}>
          <p className="text-xs text-tertiary-light">
            {t(I18nKey.INTEGRATIONS_HUB$ADMIN_REQUESTS_EMPTY)}
          </p>
        </div>
      ) : (
        <div
          className={cn("overflow-x-auto", settingsListContainerClassName)}
          data-testid="user-requests-list"
        >
          <table className="w-full min-w-max text-left">
            <thead className={settingsListTableHeadClassName}>
              <tr>
                <th className={userRequestHeaderCellClassName}>
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_INTEGRATION)}
                </th>
                <th className={userRequestHeaderCellClassName}>
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_REQUESTED_BY)}
                </th>
                <th className={userRequestHeaderCellClassName}>
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_REQUESTED)}
                </th>
                <th className={userRequestHeaderCellClassName}>
                  {t(I18nKey.INTEGRATIONS_HUB$COLUMN_NOTES)}
                </th>
                <th className={userRequestHeaderCellClassName}>
                  <span className="sr-only">
                    {t(I18nKey.INTEGRATIONS_HUB$COLUMN_ACTIONS)}
                  </span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--oh-border)]">
              {userRequests.map((request) => (
                <tr
                  key={request.id}
                  data-testid={`integrations-hub-user-request-${request.id}`}
                >
                  <td className={settingsListTableCellClassName}>
                    <div className="flex min-w-0 items-center gap-2">
                      <IntegrationProviderIcon
                        provider={request.slug}
                        size="sm"
                      />
                      <div className="min-w-0">
                        <HubTruncatedText
                          text={request.name}
                          className="text-sm text-white"
                        />
                        <p className="text-[10px] leading-4 text-muted">
                          {request.source === "catalog"
                            ? t(I18nKey.INTEGRATIONS_HUB$SOURCE_CATALOG)
                            : t(I18nKey.INTEGRATIONS_HUB$SOURCE_CUSTOM)}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td
                    className={cn(settingsListTableCellClassName, "text-white")}
                  >
                    {request.requestedBy}
                  </td>
                  <td className={settingsListTableCellClassName}>
                    <TimestampCell value={request.createdAt} />
                  </td>
                  <td className={settingsListTableCellClassName}>
                    {hasUserRequestDetails(request) ? (
                      <HubHoverCard
                        testId={`user-request-notes-${request.id}`}
                        content={<UserRequestNotesContent request={request} />}
                      >
                        <button
                          type="button"
                          aria-label={t(I18nKey.INTEGRATIONS_HUB$NOTES_ARIA)}
                          className={cn(
                            settingsListIconActionButtonClassName,
                            "text-[var(--oh-text-secondary)]",
                          )}
                        >
                          <MessageSquare
                            className="size-4"
                            aria-hidden
                            strokeWidth={2}
                          />
                        </button>
                      </HubHoverCard>
                    ) : (
                      <span className="text-white">—</span>
                    )}
                  </td>
                  <td className={settingsListTableCellClassName}>
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        data-testid={`user-request-add-${request.id}`}
                        className={cn(
                          "inline-flex h-7 cursor-pointer items-center rounded-md px-2 text-xs font-medium text-[var(--oh-success)]",
                          formControlTransitionClassName,
                          "hover:bg-[color:rgba(165,231,94,0.12)]",
                        )}
                        onClick={() => openSetup(request)}
                      >
                        {t(I18nKey.INTEGRATIONS_HUB$ADD)}
                      </button>
                      <button
                        type="button"
                        data-testid={`user-request-dismiss-${request.id}`}
                        className={cn(
                          "inline-flex h-7 cursor-pointer items-center rounded-md px-2 text-xs font-medium text-[var(--oh-danger)]",
                          formControlTransitionClassName,
                          "hover:bg-[color:rgba(231,106,94,0.12)]",
                        )}
                        onClick={() => setPendingDismissId(request.id)}
                      >
                        {t(I18nKey.INTEGRATIONS_HUB$DISMISS)}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pendingDismissRequest ? (
        <ConfirmationModal
          text={t(I18nKey.INTEGRATIONS_HUB$DISMISS_REQUEST_CONFIRM, {
            name: pendingDismissRequest.name,
          })}
          onCancel={() => setPendingDismissId(null)}
          onConfirm={() => {
            dismissUserRequest(pendingDismissRequest.id);
            setPendingDismissId(null);
          }}
        />
      ) : null}

      {selected ? (
        <CatalogConnectorModal
          integration={selected}
          onClose={closeSetup}
          onRegister={() => {
            connect(selected.slug, selected);
            if (setupRequestId) {
              dismissUserRequest(setupRequestId);
            }
          }}
          onDelete={() => {
            disconnect(selected.slug);
            closeSetup();
          }}
          onToggleEnabled={() => toggleEnabled(selected.slug)}
          onToolAccessModeChange={(toolName, mode) =>
            updateToolAccess(selected.slug, toolName, mode)
          }
        />
      ) : null}
    </div>
  );
}
