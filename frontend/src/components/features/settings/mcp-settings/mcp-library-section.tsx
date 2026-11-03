import React from "react";
import { useTranslation } from "react-i18next";
import { INTEGRATION_CATALOG } from "@openhands/extensions/integrations";
import { SearchIcon } from "#/components/shared/icons/inline-icons";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import {
  formControlInlineInputClassName,
  formControlShellClassName,
} from "#/utils/form-control-classes";
import {
  getMcpLibraryEntries,
  mcpLibraryEntryMatchesQuery,
  type McpLibraryEntry,
} from "#/utils/mcp-library";
import { cn } from "#/utils/utils";
import { McpLibraryCard } from "./mcp-library-card";

const LIBRARY_ENTRIES = getMcpLibraryEntries(INTEGRATION_CATALOG);

interface McpLibrarySectionProps {
  onSelect: (entry: McpLibraryEntry) => void;
}

/**
 * The MCP Library (OHE-3870): popular MCP servers from the OpenHands
 * integrations catalog, each installable as one of the member's own servers.
 */
export function McpLibrarySection({ onSelect }: McpLibrarySectionProps) {
  const { t } = useTranslation();
  const [query, setQuery] = React.useState("");
  const entries = LIBRARY_ENTRIES.filter((entry) =>
    mcpLibraryEntryMatchesQuery(entry, query),
  );

  return (
    <section
      data-testid="mcp-library-section"
      className="mt-2 flex flex-col gap-4 border-t border-[var(--oh-border)] pt-6"
    >
      <Typography.H2>{t(I18nKey.MCP$LIBRARY_TITLE)}</Typography.H2>

      <div className={formControlShellClassName}>
        <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
          <SearchIcon />
        </span>
        <input
          data-testid="mcp-library-search"
          type="text"
          placeholder={t(I18nKey.MCP$SEARCH_PLACEHOLDER)}
          aria-label={t(I18nKey.MCP$SEARCH_PLACEHOLDER)}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className={cn(formControlInlineInputClassName, "text-white")}
        />
      </div>

      {entries.length === 0 ? (
        <div
          data-testid="mcp-library-empty"
          className="rounded-xl border border-dashed border-[var(--oh-border)] p-6 text-center"
        >
          <p className="text-xs text-muted">{t(I18nKey.MCP$SEARCH_EMPTY)}</p>
        </div>
      ) : (
        <div
          data-testid="mcp-library-grid"
          className="grid grid-cols-1 gap-3 md:grid-cols-2"
        >
          {entries.map((entry) => (
            <McpLibraryCard
              key={entry.id}
              entry={entry}
              onSelect={() => onSelect(entry)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
