import { useTranslation } from "react-i18next";
import { StyledTooltip } from "#/components/shared/buttons/styled-tooltip";
import { I18nKey } from "#/i18n/declaration";
import { ToggleSwitchVisual } from "#/ui/toggle-switch";
import { cn } from "#/utils/utils";

interface McpSaveAsSecretToggleProps {
  fieldKey: string;
  checked: boolean;
  onToggle: (value: boolean) => void;
}

/** Also save an MCP Library field's value as a personal secret named `fieldKey`. */
export function McpSaveAsSecretToggle({
  fieldKey,
  checked,
  onToggle,
}: McpSaveAsSecretToggleProps) {
  const { t } = useTranslation();

  return (
    <label
      data-testid={`mcp-install-save-secret-${fieldKey}`}
      className={cn(
        "mt-0.5 flex cursor-pointer flex-wrap items-center gap-2 rounded-lg border px-3 py-2 transition-colors",
        checked
          ? "border-white/25 bg-white/5"
          : "border-[var(--oh-border)] bg-transparent hover:bg-white/[0.03]",
      )}
    >
      {/* The real checkbox stays in the accessibility tree behind the visual. */}
      <input
        className="sr-only"
        type="checkbox"
        checked={checked}
        onChange={(event) => onToggle(event.target.checked)}
      />
      <ToggleSwitchVisual enabled={checked} />
      <span className="text-sm text-white">
        {t(I18nKey.MCP$ALSO_SAVE_AS_SECRET)}
      </span>
      <span className="ml-auto flex min-w-0 max-w-full items-center gap-2">
        <code className="min-w-0 break-all rounded border border-[var(--oh-border)] px-1.5 py-0.5 font-mono text-xs text-white">
          {fieldKey}
        </code>
        <StyledTooltip
          content={t(I18nKey.MCP$SAVE_AS_SECRET_TOOLTIP)}
          placement="top"
        >
          <button
            type="button"
            aria-label={t(I18nKey.MCP$SAVE_AS_SECRET_TOOLTIP)}
            className="flex h-4 w-4 shrink-0 cursor-help items-center justify-center rounded-full border border-[var(--oh-muted)] text-[9px] font-bold text-white"
            onClick={(event) => event.preventDefault()}
          >
            ?
          </button>
        </StyledTooltip>
      </span>
    </label>
  );
}
