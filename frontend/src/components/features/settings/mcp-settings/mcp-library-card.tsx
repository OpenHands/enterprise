import type { KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { I18nKey } from "#/i18n/declaration";
import {
  getDefaultMcpTransport,
  type McpLibraryEntry,
} from "#/utils/mcp-library";
import { McpLogoBadge } from "./mcp-logo-badge";

const TRANSPORT_LABELS = {
  stdio: "STDIO",
  shttp: "HTTP",
  sse: "SSE",
} as const;

interface McpLibraryCardProps {
  entry: McpLibraryEntry;
  onSelect: () => void;
}

/** One catalog server; the card and its "+" both open the install dialog. */
export function McpLibraryCard({ entry, onSelect }: McpLibraryCardProps) {
  const { t } = useTranslation();
  const transport = getDefaultMcpTransport(entry);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={handleKeyDown}
      data-testid={`mcp-library-card-${entry.id}`}
      className="flex min-h-33 cursor-pointer flex-col rounded-xl border border-[var(--oh-border)] bg-base-secondary p-4 text-left transition-colors hover:bg-[var(--oh-interactive-hover)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--oh-focus)]"
    >
      <div className="flex items-start gap-3">
        <McpLogoBadge entry={entry} />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <header className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-sm font-semibold text-white">
                {entry.name}
              </h3>
              <p className="mt-0.5 text-xs text-tertiary-alt">
                {transport ? TRANSPORT_LABELS[transport.kind] : ""}
              </p>
            </div>
            <button
              type="button"
              data-testid={`mcp-library-add-${entry.id}`}
              aria-label={t(I18nKey.MCP$TOGGLE_ADD_SERVER)}
              title={t(I18nKey.MCP$TOGGLE_ADD_SERVER)}
              onClick={(event) => {
                event.stopPropagation();
                onSelect();
              }}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--oh-border)] text-white transition-colors hover:bg-[var(--oh-interactive-hover)]"
            >
              <Plus className="h-4 w-4" aria-hidden />
            </button>
          </header>
          <p className="line-clamp-3 text-xs leading-relaxed text-muted">
            {entry.description}
          </p>
        </div>
      </div>
    </div>
  );
}
