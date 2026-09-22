import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import {
  HubModal,
  hubModalBodyClassName,
  hubModalFooterClassName,
} from "#/components/features/integrations-hub/hub-modal";
import {
  legacyCutoverDisplayName,
  type LegacyCutoverItem,
} from "#/components/features/integrations-hub/legacy-integrations-cutover";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

interface IntegrationsHubCutoverModalProps {
  items: LegacyCutoverItem[];
  onClose: () => void;
  onReconnect: (hubSlug: string) => void;
}

export function IntegrationsHubCutoverModal({
  items,
  onClose,
  onReconnect,
}: IntegrationsHubCutoverModalProps) {
  const { t } = useTranslation();
  const hasItems = items.length > 0;

  return (
    <HubModal
      ariaLabel={t(I18nKey.INTEGRATIONS_HUB$CUTOVER_TITLE)}
      testId="integrations-hub-cutover-modal"
      onClose={onClose}
    >
      <div className={hubModalBodyClassName}>
        <h2 className="pr-8 text-base font-semibold text-white">
          {t(I18nKey.INTEGRATIONS_HUB$CUTOVER_TITLE)}
        </h2>
        <p className="mt-1 text-xs leading-5 text-tertiary-light">
          {t(
            hasItems
              ? I18nKey.INTEGRATIONS_HUB$CUTOVER_BODY
              : I18nKey.INTEGRATIONS_HUB$CUTOVER_BODY_NONE,
          )}
        </p>

        {hasItems ? (
          <ul
            className="mt-5 flex flex-col gap-2"
            data-testid="integrations-hub-cutover-list"
          >
            {items.map((item) => {
              const name = legacyCutoverDisplayName(item.id);
              return (
                <li
                  key={item.id}
                  data-testid={`integrations-hub-cutover-item-${item.id}`}
                  className={cn(
                    "flex flex-col gap-3 rounded-xl border border-[var(--oh-border)] bg-black/20 p-3.5 sm:flex-row sm:items-center sm:justify-between",
                  )}
                >
                  <div className="flex min-w-0 items-start gap-3">
                    <IntegrationProviderIcon provider={item.id} size="md" />
                    <div className="min-w-0 space-y-0.5">
                      <p className="text-sm font-medium text-white">{name}</p>
                      <p className="text-xs leading-5 text-tertiary-light">
                        {item.canReconnectInHub
                          ? t(I18nKey.INTEGRATIONS_HUB$CUTOVER_ITEM_HINT, {
                              name,
                            })
                          : t(
                              I18nKey.INTEGRATIONS_HUB$CUTOVER_ITEM_HINT_UNAVAILABLE,
                              { name },
                            )}
                      </p>
                    </div>
                  </div>
                  {item.canReconnectInHub && item.hubSlug ? (
                    <BrandButton
                      type="button"
                      variant="secondary"
                      className="shrink-0 self-start sm:self-center"
                      testId={`integrations-hub-cutover-reconnect-${item.id}`}
                      onClick={() => onReconnect(item.hubSlug!)}
                    >
                      {t(I18nKey.INTEGRATIONS_HUB$CUTOVER_RECONNECT)}
                    </BrandButton>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>

      <div className={hubModalFooterClassName}>
        <BrandButton
          type="button"
          variant="primary"
          testId="integrations-hub-cutover-got-it"
          onClick={onClose}
        >
          {t(I18nKey.INTEGRATIONS_HUB$CUTOVER_GOT_IT)}
        </BrandButton>
      </div>
    </HubModal>
  );
}
