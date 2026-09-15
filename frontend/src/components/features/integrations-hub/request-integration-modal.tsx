import { useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import { HubSearchField } from "#/components/features/integrations-hub/hub-search-field";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import {
  HubModal,
  hubModalBodyClassName,
  hubModalFooterClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { hubAuthLabel } from "#/components/features/integrations-hub/hub-format";
import { I18nKey } from "#/i18n/declaration";
import type {
  HubIntegration,
  HubIntegrationRequestPayload,
} from "#/types/integrations-hub";
import {
  formControlFieldClassName,
  formControlMultilineFieldClassName,
} from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

interface RequestIntegrationModalProps {
  catalog: HubIntegration[];
  onClose: () => void;
  onSubmit: (payload: HubIntegrationRequestPayload) => void;
}

export function RequestIntegrationModal({
  catalog,
  onClose,
  onSubmit,
}: RequestIntegrationModalProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"catalog" | "custom">("catalog");
  const [search, setSearch] = useState("");
  const [selectedSlugs, setSelectedSlugs] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [customName, setCustomName] = useState("");
  const [customDescription, setCustomDescription] = useState("");
  const [customDocsUrl, setCustomDocsUrl] = useState("");
  const [error, setError] = useState("");

  const query = search.trim().toLowerCase();
  const requestable = useMemo(
    () =>
      catalog.filter(
        (item) =>
          !query ||
          `${item.name} ${item.description}`.toLowerCase().includes(query),
      ),
    [catalog, query],
  );

  const tabClass = (active: boolean) =>
    cn(
      "cursor-pointer rounded-md px-3 py-1.5 text-xs font-medium",
      active
        ? "bg-white/10 text-white"
        : "text-tertiary-light hover:bg-white/5 hover:text-white",
    );

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mode === "catalog") {
      if (selectedSlugs.length === 0) {
        setError(t(I18nKey.INTEGRATIONS_HUB$REQUEST_SELECT_REQUIRED));
        return;
      }
      onSubmit({ source: "catalog", slugs: selectedSlugs, notes });
      return;
    }
    if (!customName.trim()) {
      setError(t(I18nKey.INTEGRATIONS_HUB$REQUEST_NAME_REQUIRED));
      return;
    }
    onSubmit({
      source: "custom",
      name: customName,
      description: customDescription,
      docsUrl: customDocsUrl,
      notes,
    });
  };

  return (
    <HubModal
      ariaLabel={t(I18nKey.INTEGRATIONS_HUB$REQUEST_TITLE)}
      testId="request-integration-modal"
      onClose={onClose}
    >
      <form className="flex min-h-0 flex-1 flex-col" onSubmit={handleSubmit}>
        <div className={hubModalBodyClassName}>
          <h2 className="pr-8 text-base font-semibold text-white">
            {t(I18nKey.INTEGRATIONS_HUB$REQUEST_TITLE)}
          </h2>
          <p className="mt-1 text-xs leading-5 text-tertiary-light">
            {t(I18nKey.INTEGRATIONS_HUB$REQUEST_BODY)}
          </p>

          <div
            role="tablist"
            aria-label={t(I18nKey.INTEGRATIONS_HUB$REQUEST_TABS_ARIA)}
            className="mt-5 inline-flex rounded-lg border border-[var(--oh-border)] bg-black/20 p-1"
          >
            <button
              type="button"
              role="tab"
              aria-selected={mode === "catalog"}
              data-testid="request-integration-catalog-tab"
              className={tabClass(mode === "catalog")}
              onClick={() => {
                setMode("catalog");
                setError("");
              }}
            >
              {t(I18nKey.INTEGRATIONS_HUB$SOURCE_CATALOG)}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "custom"}
              data-testid="request-integration-custom-tab"
              className={tabClass(mode === "custom")}
              onClick={() => {
                setMode("custom");
                setError("");
              }}
            >
              {t(I18nKey.INTEGRATIONS_HUB$SOURCE_CUSTOM)}
            </button>
          </div>

          {mode === "catalog" ? (
            <div className="mt-4 space-y-3">
              <HubSearchField
                value={search}
                onChange={setSearch}
                placeholder={t(I18nKey.INTEGRATIONS_HUB$REQUEST_SEARCH)}
                testId="request-integration-search"
              />
              {requestable.length === 0 ? (
                <p
                  data-testid="request-integration-empty"
                  className="rounded-lg border border-dashed border-[var(--oh-border)] px-3 py-4 text-sm text-tertiary-light"
                >
                  {t(I18nKey.INTEGRATIONS_HUB$REQUEST_CATALOG_EMPTY)}
                </p>
              ) : (
                <ul
                  data-testid="request-integration-list"
                  className="max-h-64 divide-y divide-[var(--oh-border)] overflow-y-auto rounded-xl border border-[var(--oh-border)]"
                >
                  {requestable.map((entry) => {
                    const isSelected = selectedSlugs.includes(entry.slug);
                    return (
                      <li key={entry.slug}>
                        <button
                          type="button"
                          data-testid={`request-integration-option-${entry.slug}`}
                          aria-pressed={isSelected}
                          onClick={() => {
                            setSelectedSlugs((current) =>
                              current.includes(entry.slug)
                                ? current.filter((slug) => slug !== entry.slug)
                                : [...current, entry.slug],
                            );
                            setError("");
                          }}
                          className={cn(
                            "flex w-full cursor-pointer items-start gap-3 px-3 py-3 text-left hover:bg-[var(--oh-interactive-hover)]",
                            isSelected && "bg-[var(--oh-interactive-hover)]",
                          )}
                        >
                          <input
                            type="checkbox"
                            readOnly
                            tabIndex={-1}
                            checked={isSelected}
                            className="pointer-events-none mt-1"
                          />
                          <IntegrationProviderIcon
                            provider={entry.slug}
                            logoUrl={entry.logoUrl}
                            size="sm"
                          />
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-2">
                              <HubTruncatedText
                                text={entry.name}
                                className="text-sm font-medium text-white"
                              />
                              {hubAuthLabel(entry.authStrategy, t) ? (
                                <span className="text-xs text-tertiary-light">
                                  {hubAuthLabel(entry.authStrategy, t)}
                                </span>
                              ) : null}
                            </span>
                            <HubTruncatedText
                              text={entry.description}
                              lines={2}
                              className="mt-0.5 text-xs leading-5 text-tertiary-light"
                            />
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          ) : (
            <div className="mt-4 grid gap-3">
              <label className="grid gap-1.5 text-sm text-white">
                <span className="font-medium">
                  {t(I18nKey.INTEGRATIONS_HUB$REQUEST_NAME)}
                </span>
                <input
                  data-testid="request-integration-custom-name"
                  className={formControlFieldClassName}
                  placeholder={t(
                    I18nKey.INTEGRATIONS_HUB$REQUEST_NAME_PLACEHOLDER,
                  )}
                  value={customName}
                  onChange={(event) => setCustomName(event.target.value)}
                />
              </label>
              <label className="grid gap-1.5 text-sm text-white">
                <span className="font-medium">
                  {t(I18nKey.INTEGRATIONS_HUB$REQUEST_CUSTOM_DESCRIPTION)}
                </span>
                <textarea
                  data-testid="request-integration-custom-description"
                  className={cn(formControlMultilineFieldClassName, "min-h-24")}
                  value={customDescription}
                  onChange={(event) => setCustomDescription(event.target.value)}
                />
              </label>
              <label className="grid gap-1.5 text-sm text-white">
                <span className="font-medium">
                  {t(I18nKey.INTEGRATIONS_HUB$REQUEST_DOCS_URL)}
                </span>
                <input
                  data-testid="request-integration-custom-docs-url"
                  className={formControlFieldClassName}
                  placeholder={t(
                    I18nKey.INTEGRATIONS_HUB$REQUEST_DOCS_PLACEHOLDER,
                  )}
                  value={customDocsUrl}
                  onChange={(event) => setCustomDocsUrl(event.target.value)}
                />
              </label>
            </div>
          )}

          <label className="mt-4 grid gap-1.5 text-sm text-white">
            <span className="font-medium">
              {t(I18nKey.INTEGRATIONS_HUB$REQUEST_NOTES)}
            </span>
            <textarea
              data-testid="request-integration-notes"
              className={cn(formControlMultilineFieldClassName, "min-h-24")}
              placeholder={t(
                I18nKey.INTEGRATIONS_HUB$REQUEST_NOTES_PLACEHOLDER,
              )}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </label>
          {error ? (
            <p className="mt-4 text-sm text-[var(--oh-danger)]" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className={hubModalFooterClassName}>
          {mode === "catalog" && selectedSlugs.length > 0 ? (
            <p className="mr-auto text-xs text-tertiary-light">
              {t(I18nKey.INTEGRATIONS_HUB$REQUEST_SELECTED, {
                count: selectedSlugs.length,
              })}
            </p>
          ) : null}
          <BrandButton type="button" variant="secondary" onClick={onClose}>
            {t(I18nKey.BUTTON$CANCEL)}
          </BrandButton>
          <BrandButton
            type="submit"
            variant="primary"
            testId="request-integration-submit"
          >
            {t(I18nKey.INTEGRATIONS_HUB$REQUEST_SUBMIT)}
          </BrandButton>
        </div>
      </form>
    </HubModal>
  );
}
