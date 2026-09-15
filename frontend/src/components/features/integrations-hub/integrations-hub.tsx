import { useMemo, useRef, useState } from "react";
import { Check, MoreVertical } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { ConnectWizardModal } from "#/components/features/integrations-hub/connect-wizard-modal";
import { HubCountBadge } from "#/components/features/integrations-hub/hub-count-badge";
import { HubIntegrationCard } from "#/components/features/integrations-hub/hub-integration-card";
import { HubSearchField } from "#/components/features/integrations-hub/hub-search-field";
import {
  hubCardGridClassName,
  hubEmptyStateClassName,
  hubSearchEmptyStateClassName,
} from "#/components/features/integrations-hub/hub-card-classes";
import { IntegrationDetailModal } from "#/components/features/integrations-hub/integration-detail-modal";
import { IntegrationsHubPageHeader } from "#/components/features/integrations-hub/integrations-hub-page-header";
import { RequestIntegrationModal } from "#/components/features/integrations-hub/request-integration-modal";
import {
  HubModal,
  hubModalBodyClassName,
  hubModalFooterClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { useIntegrationsHubStub } from "#/hooks/query/use-integrations-hub-stub";
import { I18nKey } from "#/i18n/declaration";
import type { HubIntegration } from "#/types/integrations-hub";
import {
  dropdownMenuListClassName,
  dropdownMenuPanelPaddingClassName,
  dropdownMenuRowClassName,
} from "#/utils/dropdown-classes";
import { formControlFieldClassName } from "#/utils/form-control-classes";
import { unusedWindowMs } from "#/components/features/integrations-hub/hub-format";
import { displaySuccessToast } from "#/utils/custom-toast-handlers";
import { cn } from "#/utils/utils";

function InstalledIntegrationsBody({
  installedCount,
  installed,
  onToggle,
  onOpen,
}: {
  installedCount: number;
  installed: HubIntegration[];
  onToggle: (slug: string) => void;
  onOpen: (slug: string) => void;
}) {
  const { t } = useTranslation();

  if (installedCount === 0) {
    return (
      <div
        data-testid="integrations-hub-installed-empty"
        className={hubEmptyStateClassName}
      >
        <p className="text-sm text-white">
          {t(I18nKey.INTEGRATIONS_HUB$INSTALLED_EMPTY)}
        </p>
        <p className="mt-1 text-xs text-tertiary-light">
          {t(I18nKey.INTEGRATIONS_HUB$INSTALLED_EMPTY_HINT)}
        </p>
      </div>
    );
  }

  if (installed.length === 0) {
    return (
      <div className={hubSearchEmptyStateClassName}>
        <p className="text-xs text-tertiary-light">
          {t(I18nKey.INTEGRATIONS_HUB$EMPTY)}
        </p>
      </div>
    );
  }

  return (
    <div className="@container min-w-0 w-full">
      <div
        data-testid="installed-integrations-grid"
        className={hubCardGridClassName}
      >
        {installed.map((integration) => (
          <HubIntegrationCard
            key={integration.slug}
            integration={integration}
            isInstalled
            onToggle={() => onToggle(integration.slug)}
            onOpen={() => onOpen(integration.slug)}
          />
        ))}
      </div>
    </div>
  );
}

export function IntegrationsHub() {
  const { t } = useTranslation();
  const hub = useIntegrationsHubStub();
  const [search, setSearch] = useState("");
  const [wizardSlug, setWizardSlug] = useState<string | null | undefined>();
  const [detailSlug, setDetailSlug] = useState<string | null>(null);
  const [requestOpen, setRequestOpen] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [hideDisabled, setHideDisabled] = useState(false);
  const [bulkDisableOpen, setBulkDisableOpen] = useState(false);
  const [unusedDays, setUnusedDays] = useState("30");
  const [unusedUnit, setUnusedUnit] = useState<"days" | "weeks" | "months">(
    "days",
  );
  const actionsRef = useRef<HTMLButtonElement>(null);

  const query = search.trim().toLowerCase();
  const installed = useMemo(
    () =>
      hub.integrations.filter(
        (item) =>
          item.connected &&
          (!query ||
            `${item.name} ${item.description} ${item.kind}`
              .toLowerCase()
              .includes(query)),
      ),
    [hub.integrations, query],
  );
  const available = useMemo(
    () =>
      hub.integrations.filter(
        (item) =>
          !item.connected &&
          (!query ||
            `${item.name} ${item.description} ${item.kind}`
              .toLowerCase()
              .includes(query)),
      ),
    [hub.integrations, query],
  );
  const installedCount = hub.integrations.filter(
    (item) => item.connected,
  ).length;
  const visibleToolsFor = (item: HubIntegration) =>
    hideDisabled
      ? item.tools.filter((tool) => tool.accessMode !== "disabled")
      : item.tools;
  const visibleToolCount = installed.reduce((sum, item) => {
    const visible = visibleToolsFor(item).length;
    if (visible > 0) {
      return sum + visible;
    }
    return hideDisabled ? sum : sum + item.toolCount;
  }, 0);
  const unusedCutoffMs = useMemo(() => {
    const value = Math.min(Math.max(Number(unusedDays) || 30, 1), 3650);
    return Date.now() - unusedWindowMs(value, unusedUnit);
  }, [unusedDays, unusedUnit]);
  const unusedToolCandidateCount = hub.integrations.reduce(
    (sum, item) =>
      sum +
      item.tools.filter(
        (tool) =>
          tool.accessMode !== "disabled" &&
          tool.lastUsedAt &&
          new Date(tool.lastUsedAt).getTime() < unusedCutoffMs,
      ).length,
    0,
  );
  const detail =
    hub.integrations.find((item) => item.slug === detailSlug) ?? null;

  return (
    <div className="flex flex-col gap-6" data-testid="integrations-hub-screen">
      <IntegrationsHubPageHeader
        title={I18nKey.SETTINGS$NAV_INTEGRATIONS}
        subtitle={I18nKey.INTEGRATIONS_HUB$PAGE_INTEGRATIONS_SUBLINE}
        subtitleExtra={
          hub.showRequestButton ? (
            <>
              {" "}
              {t(I18nKey.INTEGRATIONS_HUB$CANT_FIND_PROMPT)}{" "}
              <button
                type="button"
                data-testid="integrations-request-link"
                onClick={() => setRequestOpen(true)}
                className="cursor-pointer border-0 bg-transparent p-0 text-sm text-white hover:opacity-80"
              >
                {t(I18nKey.INTEGRATIONS_HUB$CANT_FIND_LINK)}
              </button>
              .
            </>
          ) : null
        }
        actions={
          hub.showRequestButton ? (
            <BrandButton
              type="button"
              variant="secondary"
              testId="request-integration-button"
              onClick={() => setRequestOpen(true)}
            >
              {t(I18nKey.INTEGRATIONS_HUB$REQUEST)}
            </BrandButton>
          ) : null
        }
      />
      <HubSearchField
        value={search}
        onChange={setSearch}
        placeholder={t(I18nKey.INTEGRATIONS_HUB$SEARCH)}
        testId="integrations-hub-search"
      />

      <section data-testid="your-integrations-section">
        <div className="flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center">
            <h2 className="text-base font-semibold text-foreground">
              {t(I18nKey.INTEGRATIONS_HUB$YOUR_INTEGRATIONS)}
            </h2>
            <div className="ml-2 flex items-center gap-3">
              <HubCountBadge
                count={installed.length || installedCount}
                className="ml-0"
              />
              {installedCount > 0 ? (
                <HubCountBadge
                  count={visibleToolCount}
                  label={t(I18nKey.INTEGRATIONS_HUB$TOOLS_LABEL)}
                  className="ml-0"
                />
              ) : null}
            </div>
          </div>
          {installedCount > 0 ? (
            <div className="relative shrink-0">
              <button
                ref={actionsRef}
                type="button"
                data-testid="integrations-section-actions-trigger"
                aria-label={t(I18nKey.INTEGRATIONS_HUB$SECTION_ACTIONS)}
                aria-haspopup="menu"
                aria-expanded={actionsOpen}
                onClick={() => setActionsOpen((open) => !open)}
                className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md border border-[var(--oh-border)] bg-base-secondary text-[var(--oh-muted)] hover:bg-[var(--oh-interactive-hover)] hover:text-white"
              >
                <MoreVertical className="size-4" aria-hidden strokeWidth={2} />
              </button>
              {actionsOpen ? (
                <div
                  role="menu"
                  className={cn(
                    "absolute right-0 top-full z-50 mt-1 w-max",
                    "rounded-[6px] bg-tertiary context-menu-box-shadow",
                    dropdownMenuPanelPaddingClassName,
                    dropdownMenuListClassName,
                  )}
                >
                  <button
                    type="button"
                    role="menuitemcheckbox"
                    data-testid="integrations-hide-disabled-tools-toggle"
                    aria-checked={hideDisabled}
                    className={dropdownMenuRowClassName}
                    onClick={() => {
                      setHideDisabled((current) => !current);
                      setActionsOpen(false);
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {t(I18nKey.INTEGRATIONS_HUB$HIDE_DISABLED)}
                    </span>
                    {hideDisabled ? <Check size={14} aria-hidden /> : null}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    data-testid="integrations-disable-unused-tools-menu-item"
                    className={dropdownMenuRowClassName}
                    onClick={() => {
                      setActionsOpen(false);
                      setBulkDisableOpen(true);
                    }}
                  >
                    {t(I18nKey.INTEGRATIONS_HUB$DISABLE_UNUSED)}
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="mt-3">
          <InstalledIntegrationsBody
            installedCount={installedCount}
            installed={installed}
            onToggle={hub.toggleEnabled}
            onOpen={setDetailSlug}
          />
        </div>
      </section>

      <section data-testid="integration-catalog-section">
        <div className="flex items-center">
          <h2 className="text-base font-semibold text-foreground">
            {t(I18nKey.INTEGRATIONS_HUB$AVAILABLE_INTEGRATIONS)}
          </h2>
          <HubCountBadge count={available.length} />
        </div>
        <div className="mt-3">
          {available.length === 0 ? (
            <div
              data-testid="integrations-hub-empty"
              className={hubSearchEmptyStateClassName}
            >
              <p className="text-xs text-tertiary-light">
                {t(I18nKey.INTEGRATIONS_HUB$EMPTY)}
              </p>
            </div>
          ) : (
            <div className="@container min-w-0 w-full">
              <div
                data-testid="integration-catalog-grid"
                className={hubCardGridClassName}
              >
                {available.map((integration) => (
                  <HubIntegrationCard
                    key={integration.slug}
                    integration={integration}
                    isInstalled={false}
                    onToggle={() => undefined}
                    onOpen={() => setWizardSlug(integration.slug)}
                  />
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {wizardSlug !== undefined ? (
        <ConnectWizardModal
          connectors={available}
          initialSlug={wizardSlug}
          onClose={() => setWizardSlug(undefined)}
          onCreate={(slug) => {
            hub.connect(slug);
            setWizardSlug(undefined);
          }}
        />
      ) : null}

      {detail ? (
        <IntegrationDetailModal
          integration={detail}
          hideDisabledTools={hideDisabled}
          onClose={() => setDetailSlug(null)}
          onToggleEnabled={() => hub.toggleEnabled(detail.slug)}
          onDelete={() => {
            hub.disconnect(detail.slug);
            setDetailSlug(null);
          }}
          onUpdateToolAccess={(toolName, mode) =>
            hub.updateToolAccess(detail.slug, toolName, mode)
          }
        />
      ) : null}

      {requestOpen ? (
        <RequestIntegrationModal
          catalog={hub.requestableCatalog}
          onClose={() => setRequestOpen(false)}
          onSubmit={(payload) => {
            hub.requestIntegration(payload);
            setRequestOpen(false);
            displaySuccessToast(t(I18nKey.INTEGRATIONS_HUB$REQUEST_SENT));
          }}
        />
      ) : null}

      {bulkDisableOpen ? (
        <HubModal
          ariaLabel={t(I18nKey.INTEGRATIONS_HUB$DISABLE_UNUSED)}
          testId="unused-tools-bulk-disable-modal"
          onClose={() => setBulkDisableOpen(false)}
        >
          <div className={hubModalBodyClassName}>
            <h2 className="pr-8 text-base font-semibold text-white">
              {t(I18nKey.INTEGRATIONS_HUB$BULK_DISABLE_TITLE)}
            </h2>
            <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
              {t(I18nKey.INTEGRATIONS_HUB$BULK_DISABLE_BODY)}
            </p>
            <div className="mt-6 flex flex-col gap-4 md:flex-row md:items-end md:gap-4">
              <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
                <span>
                  {t(I18nKey.INTEGRATIONS_HUB$BULK_DISABLE_UNUSED_FOR)}
                </span>
                <input
                  type="number"
                  min={1}
                  max={3650}
                  value={unusedDays}
                  aria-label="Unused threshold value"
                  onChange={(event) => setUnusedDays(event.target.value)}
                  className={cn(formControlFieldClassName, "w-28")}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
                <span>{t(I18nKey.INTEGRATIONS_HUB$UNUSED_WINDOW)}</span>
                <select
                  data-testid="unused-tools-threshold-unit-select"
                  aria-label="Unused threshold unit"
                  className={cn(formControlFieldClassName, "w-32")}
                  value={unusedUnit}
                  onChange={(event) =>
                    setUnusedUnit(
                      event.target.value as "days" | "weeks" | "months",
                    )
                  }
                >
                  <option value="days">
                    {t(I18nKey.INTEGRATIONS_HUB$UNUSED_DAYS)}
                  </option>
                  <option value="weeks">
                    {t(I18nKey.INTEGRATIONS_HUB$UNUSED_WEEKS)}
                  </option>
                  <option value="months">
                    {t(I18nKey.INTEGRATIONS_HUB$UNUSED_MONTHS)}
                  </option>
                </select>
              </label>
              <p
                data-testid="unused-tools-matched-count"
                className="text-xs text-[var(--oh-text-secondary)] md:ml-auto md:pb-2"
              >
                {t(I18nKey.INTEGRATIONS_HUB$TOOLS_MATCHED, {
                  count: unusedToolCandidateCount,
                })}
              </p>
            </div>
            <div
              className={cn(hubModalFooterClassName, "mt-6 border-t-0 px-0")}
            >
              <BrandButton
                type="button"
                variant="secondary"
                onClick={() => {
                  hub.disableUnusedTools(Number(unusedDays) || 30, unusedUnit);
                  setBulkDisableOpen(false);
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$DISABLE)}
              </BrandButton>
            </div>
          </div>
        </HubModal>
      ) : null}
    </div>
  );
}
