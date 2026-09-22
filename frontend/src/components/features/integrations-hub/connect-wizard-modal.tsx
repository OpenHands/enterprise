import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, PlugZap } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { HubIntegrationModalHeader } from "#/components/features/integrations-hub/hub-integration-modal-header";
import { HubToolAccessList } from "#/components/features/integrations-hub/hub-tool-access-list";
import {
  HubModal,
  hubModalFooterClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { I18nKey } from "#/i18n/declaration";
import type { HubIntegration } from "#/types/integrations-hub";
import { formControlFieldClassName } from "#/utils/form-control-classes";

interface ConnectWizardModalProps {
  connectors: HubIntegration[];
  initialSlug?: string | null;
  onClose: () => void;
  onCreate: (slug: string) => void;
}

export function ConnectWizardModal({
  connectors,
  initialSlug = null,
  onClose,
  onCreate,
}: ConnectWizardModalProps) {
  const { t } = useTranslation();
  const [slug] = useState(initialSlug ?? connectors[0]?.slug ?? "");
  const [displayName, setDisplayName] = useState(
    () => connectors.find((item) => item.slug === initialSlug)?.name ?? "",
  );
  const [integrationKey, setIntegrationKey] = useState(initialSlug ?? "");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [step, setStep] = useState<"connect" | "review">("connect");
  const selected = useMemo(
    () => connectors.find((item) => item.slug === slug) ?? null,
    [connectors, slug],
  );

  return (
    <HubModal
      ariaLabel={t(I18nKey.INTEGRATIONS_HUB$WIZARD_ARIA)}
      testId="integration-wizard-modal"
      width="xl"
      className="gap-0"
      onClose={onClose}
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {step === "connect" ? (
          <>
            <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-7">
              {selected ? (
                <HubIntegrationModalHeader
                  integration={selected}
                  showDescription
                  testId="integration-wizard-header"
                />
              ) : (
                <header
                  className="flex items-start gap-3"
                  data-testid="integration-wizard-header"
                >
                  <PlugZap className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
                  <div className="space-y-1">
                    <h2 className="text-lg font-semibold text-white">
                      {t(I18nKey.INTEGRATIONS_HUB$WIZARD_TITLE)}
                    </h2>
                    <p className="text-sm leading-6 text-tertiary-light">
                      {t(I18nKey.INTEGRATIONS_HUB$WIZARD_BODY)}
                    </p>
                  </div>
                </header>
              )}

              <div className="mt-6 space-y-4">
                {selected ? (
                  <div className="rounded-lg border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] p-3.5 text-sm text-tertiary-light">
                    <div className="flex flex-col gap-2.5 md:flex-row md:items-center md:justify-between">
                      <div className="space-y-0.5">
                        <p className="text-sm font-medium text-white">
                          {t(I18nKey.INTEGRATIONS_HUB$WIZARD_CONNECT_ACCOUNT, {
                            name: selected.name,
                          })}
                        </p>
                        <p className="text-xs leading-5 text-tertiary-light">
                          {t(
                            I18nKey.INTEGRATIONS_HUB$WIZARD_CONNECT_ACCOUNT_BODY,
                            { name: selected.name },
                          )}
                        </p>
                      </div>
                      <BrandButton
                        type="button"
                        variant="primary"
                        className="shrink-0"
                        testId="wizard-connect-account"
                        onClick={() => {
                          if (slug) {
                            onCreate(slug);
                            onClose();
                          }
                        }}
                      >
                        {t(I18nKey.INTEGRATIONS_HUB$CONNECT)}
                      </BrandButton>
                    </div>
                  </div>
                ) : null}

                {selected ? (
                  <div className="space-y-3">
                    <button
                      type="button"
                      aria-expanded={showAdvanced}
                      className="inline-flex cursor-pointer items-center gap-1 text-xs text-tertiary-light hover:text-white"
                      onClick={() => setShowAdvanced((current) => !current)}
                    >
                      {showAdvanced ? (
                        <ChevronDown aria-hidden className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronRight aria-hidden className="h-3.5 w-3.5" />
                      )}
                      {t(I18nKey.INTEGRATIONS_HUB$WIZARD_ADVANCED)}
                    </button>
                    {showAdvanced ? (
                      <div className="grid gap-3 md:grid-cols-2 md:items-start">
                        <label className="grid gap-1.5 text-sm text-white">
                          <span className="font-medium">
                            {t(I18nKey.INTEGRATIONS_HUB$WIZARD_INTEGRATION_KEY)}
                          </span>
                          <input
                            className={formControlFieldClassName}
                            value={integrationKey}
                            onChange={(event) =>
                              setIntegrationKey(event.target.value)
                            }
                          />
                          <span className="text-xs leading-5 text-tertiary-light">
                            {t(
                              I18nKey.INTEGRATIONS_HUB$WIZARD_INTEGRATION_KEY_HINT,
                            )}
                          </span>
                        </label>
                        <label className="grid gap-1.5 text-sm text-white">
                          <span className="font-medium">
                            {t(I18nKey.INTEGRATIONS_HUB$WIZARD_DISPLAY_NAME)}
                          </span>
                          <input
                            className={formControlFieldClassName}
                            value={displayName}
                            onChange={(event) =>
                              setDisplayName(event.target.value)
                            }
                          />
                        </label>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
            <div className={hubModalFooterClassName}>
              <BrandButton type="button" variant="secondary" onClick={onClose}>
                {t(I18nKey.BUTTON$CANCEL)}
              </BrandButton>
              <BrandButton
                type="button"
                variant="primary"
                testId="wizard-review-tools"
                isDisabled={!slug}
                onClick={() => setStep("review")}
              >
                {t(I18nKey.INTEGRATIONS_HUB$WIZARD_REVIEW_TOOLS)}
              </BrandButton>
            </div>
          </>
        ) : (
          <>
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-7 pb-4 pt-7">
              {selected ? (
                <HubIntegrationModalHeader
                  integration={selected}
                  showDescription
                  testId="integration-wizard-header"
                />
              ) : null}
              <div
                className={
                  selected
                    ? "mt-6 flex min-h-0 flex-1 flex-col"
                    : "flex min-h-0 flex-1 flex-col"
                }
              >
                <HubToolAccessList
                  tools={selected?.tools ?? []}
                  searchTestId="wizard-tools-search"
                />
              </div>
            </div>
            <div className={hubModalFooterClassName}>
              <BrandButton
                type="button"
                variant="secondary"
                onClick={() => setStep("connect")}
              >
                {t(I18nKey.INTEGRATIONS_HUB$WIZARD_BACK)}
              </BrandButton>
              <BrandButton
                type="button"
                variant="primary"
                testId="wizard-create-integration"
                isDisabled={!slug}
                onClick={() => {
                  if (slug) {
                    onCreate(slug);
                  }
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$WIZARD_CREATE)}
              </BrandButton>
            </div>
          </>
        )}
      </div>
    </HubModal>
  );
}
