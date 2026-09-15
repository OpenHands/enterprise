import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { HubSearchField } from "#/components/features/integrations-hub/hub-search-field";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { AccessModeDropdown } from "#/components/features/integrations-hub/integration-detail-modal";
import { I18nKey } from "#/i18n/declaration";
import type { HubTool, HubToolAccessMode } from "#/types/integrations-hub";

interface HubToolAccessListProps {
  tools: HubTool[];
  searchTestId: string;
  onUpdateToolAccess?: (toolName: string, mode: HubToolAccessMode) => void;
}

export function HubToolAccessList({
  tools,
  searchTestId,
  onUpdateToolAccess,
}: HubToolAccessListProps) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const query = search.trim().toLowerCase();
  const visibleTools = useMemo(
    () =>
      tools.filter(
        (tool) =>
          !query ||
          `${tool.name} ${tool.description}`.toLowerCase().includes(query),
      ),
    [query, tools],
  );

  return (
    <div className="space-y-4">
      <HubSearchField
        value={search}
        onChange={setSearch}
        placeholder={t(I18nKey.INTEGRATIONS_HUB$WIZARD_SEARCH_TOOLS)}
        testId={searchTestId}
      />
      <div className="overflow-hidden rounded-xl border border-[var(--oh-border)] bg-[var(--oh-bg-input)]">
        {tools.length === 0 || visibleTools.length === 0 ? (
          <p className="px-4 py-4 text-sm text-[var(--oh-text-secondary)]">
            {t(I18nKey.INTEGRATIONS_HUB$NO_TOOLS)}
          </p>
        ) : (
          <div className="divide-y divide-[var(--oh-border)]">
            {visibleTools.map((tool) => (
              <div
                key={tool.name}
                className="px-4 py-4"
                data-tool-name={tool.name}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-white">
                      {tool.name}
                    </p>
                    <HubTruncatedText
                      text={
                        tool.description ||
                        t(I18nKey.INTEGRATIONS_HUB$TOOL_NO_DESCRIPTION)
                      }
                      className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]"
                    />
                  </div>
                  <AccessModeDropdown
                    toolName={tool.name}
                    mode={tool.accessMode}
                    onChange={(mode) => onUpdateToolAccess?.(tool.name, mode)}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
