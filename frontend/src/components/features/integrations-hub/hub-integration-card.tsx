import { useTranslation } from "react-i18next";
import { HubCirclePlusCheckToggle } from "#/components/features/integrations-hub/hub-circle-plus-check-toggle";
import {
  hubCardInteractiveClassName,
  hubCardPillClassName,
  hubCardSurfaceClassName,
} from "#/components/features/integrations-hub/hub-card-classes";
import {
  hubAuthLabel,
  hubIntegrationMetaPills,
} from "#/components/features/integrations-hub/hub-format";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import { I18nKey } from "#/i18n/declaration";
import type { HubIntegration } from "#/types/integrations-hub";
import { cn } from "#/utils/utils";

interface HubIntegrationCardProps {
  integration: HubIntegration;
  isInstalled: boolean;
  isSelected?: boolean;
  onToggle: () => void;
  onOpen: () => void;
  testId?: string;
  toggleTestId?: string;
  ariaHasPopup?: boolean | "dialog";
  ariaExpanded?: boolean;
}

export function HubIntegrationCard({
  integration,
  isInstalled,
  isSelected = isInstalled && integration.enabled,
  onToggle,
  onOpen,
  testId = `integrations-hub-row-${integration.slug}`,
  toggleTestId,
  ariaHasPopup,
  ariaExpanded,
}: HubIntegrationCardProps) {
  const { t } = useTranslation();
  const authLabel = hubAuthLabel(integration.authStrategy, t);
  const pills = isInstalled ? hubIntegrationMetaPills(integration) : [];
  const resolvedToggleTestId =
    toggleTestId ??
    (isInstalled
      ? `integrations-hub-toggle-${integration.slug}`
      : `integrations-hub-connect-${integration.slug}`);

  return (
    <div
      data-testid={testId}
      role="button"
      tabIndex={0}
      aria-haspopup={ariaHasPopup}
      aria-expanded={ariaExpanded}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      className={cn(
        "flex min-w-0 cursor-pointer flex-col overflow-hidden p-4 text-left",
        !isInstalled && "min-h-[132px]",
        hubCardSurfaceClassName,
        hubCardInteractiveClassName,
      )}
    >
      <div className="flex items-start gap-3">
        <IntegrationProviderIcon
          provider={integration.slug}
          logoUrl={integration.logoUrl}
          size="md"
        />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <header className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <h3 className="min-w-0 text-sm font-semibold text-white">
                <HubTruncatedText text={integration.name} />
              </h3>
              {authLabel ? (
                <p className="mt-0.5 text-xs text-tertiary-alt">{authLabel}</p>
              ) : null}
              {isInstalled ? (
                <span
                  data-testid={`integrations-hub-status-${integration.slug}`}
                  className="sr-only"
                >
                  {integration.enabled
                    ? t(I18nKey.STATUS$CONNECTED)
                    : t(I18nKey.INTEGRATIONS_HUB$DISABLE)}
                </span>
              ) : null}
            </div>
            <HubCirclePlusCheckToggle
              testId={resolvedToggleTestId}
              isSelected={isSelected}
              onToggle={(selected) => {
                if (isInstalled) {
                  onToggle();
                  return;
                }
                if (selected) {
                  onOpen();
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
          </header>
          {integration.description ? (
            <HubTruncatedText
              text={integration.description}
              lines={3}
              className="text-xs leading-relaxed text-tertiary-light"
            />
          ) : null}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={hubCardPillClassName}>
              {t(I18nKey.INTEGRATIONS_HUB$TOOLS_COUNT, {
                count: integration.tools.length || integration.toolCount,
              })}
            </span>
            {pills.map((label) => (
              <span key={label} className={hubCardPillClassName}>
                {label}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
