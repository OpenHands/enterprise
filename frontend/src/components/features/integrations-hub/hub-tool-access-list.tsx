import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { HubSearchField } from "#/components/features/integrations-hub/hub-search-field";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { clampHubAccessMode } from "#/components/features/integrations-hub/hub-format";
import { AccessModeDropdown } from "#/components/features/integrations-hub/integration-detail-modal";
import { I18nKey } from "#/i18n/declaration";
import type { HubTool, HubToolAccessMode } from "#/types/integrations-hub";
import {
  dropdownMenuListClassName,
  dropdownMenuPanelPaddingClassName,
  dropdownMenuRowClassName,
} from "#/utils/dropdown-classes";
import { formControlButtonClassName } from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

interface HubToolAccessListProps {
  tools: HubTool[];
  searchTestId: string;
  showUsage?: boolean;
  toolbarStart?: ReactNode;
  toolbarEnd?: ReactNode;
  onUpdateToolAccess?: (toolName: string, mode: HubToolAccessMode) => void;
}

function toolMatchesQuery(tool: HubTool, query: string) {
  if (!query) {
    return true;
  }
  return `${tool.name} ${tool.description} ${tool.defaultScopes.join(" ")}`
    .toLowerCase()
    .includes(query);
}

function ToolDetailsCaret({
  expanded,
  controlsId,
  onToggle,
}: {
  expanded: boolean;
  controlsId: string;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      aria-expanded={expanded}
      aria-controls={controlsId}
      aria-label={
        expanded
          ? t(I18nKey.INTEGRATIONS_HUB$HIDE_TOOL_DETAILS)
          : t(I18nKey.INTEGRATIONS_HUB$SHOW_TOOL_DETAILS)
      }
      data-testid={`${controlsId}-trigger`}
      onClick={onToggle}
      className="inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-tertiary-alt hover:bg-[var(--oh-interactive-hover)] hover:text-white"
    >
      <ChevronDown
        aria-hidden
        className={cn("size-4 shrink-0", expanded && "rotate-180")}
      />
    </button>
  );
}

function ToolAccessBulkMenu({
  testId,
  canSelectAll,
  canDeselect,
  canChangeAccess,
  onSelectAll,
  onDeselect,
  onEnableSelected,
  onCaseByCaseSelected,
  onDisableSelected,
}: {
  testId: string;
  canSelectAll: boolean;
  canDeselect: boolean;
  canChangeAccess: boolean;
  onSelectAll: () => void;
  onDeselect: () => void;
  onEnableSelected: () => void;
  onCaseByCaseSelected: () => void;
  onDisableSelected: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const isDisabled = !canSelectAll && !canDeselect && !canChangeAccess;

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onMouseDown = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  const close = () => setOpen(false);

  return (
    <div ref={containerRef} className="relative shrink-0" data-testid={testId}>
      <button
        type="button"
        data-testid={`${testId}-trigger`}
        disabled={isDisabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t(I18nKey.INTEGRATIONS_HUB$BULK_ACTIONS)}
        onClick={() => setOpen((current) => !current)}
        className={cn(
          formControlButtonClassName,
          "shrink-0 whitespace-nowrap border border-[var(--oh-border)] bg-base-secondary text-white hover:bg-surface-raised",
        )}
      >
        <span>{t(I18nKey.INTEGRATIONS_HUB$BULK_ACTIONS)}</span>
        <ChevronDown
          className={cn("size-4 shrink-0", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && !isDisabled ? (
        <div
          role="menu"
          data-testid={`${testId}-panel`}
          className={cn(
            "absolute right-0 top-full z-50 mt-1 w-max min-w-full",
            "rounded-[6px] bg-tertiary context-menu-box-shadow",
            dropdownMenuPanelPaddingClassName,
          )}
        >
          <ul className={dropdownMenuListClassName}>
            <li>
              <button
                type="button"
                role="menuitem"
                data-testid={`${testId}-select-all`}
                disabled={!canSelectAll}
                className={dropdownMenuRowClassName}
                onClick={() => {
                  onSelectAll();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$SELECT_ALL)}
              </button>
            </li>
            <li>
              <button
                type="button"
                role="menuitem"
                data-testid={`${testId}-deselect`}
                disabled={!canDeselect}
                className={dropdownMenuRowClassName}
                onClick={() => {
                  onDeselect();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$DESELECT_ALL)}
              </button>
            </li>
            <li>
              <button
                type="button"
                role="menuitem"
                data-testid={`${testId}-enable`}
                disabled={!canChangeAccess}
                className={dropdownMenuRowClassName}
                onClick={() => {
                  onEnableSelected();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$ENABLE_SELECTED)}
              </button>
            </li>
            <li>
              <button
                type="button"
                role="menuitem"
                data-testid={`${testId}-approval`}
                disabled={!canChangeAccess}
                className={dropdownMenuRowClassName}
                onClick={() => {
                  onCaseByCaseSelected();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$CASE_BY_CASE_SELECTED)}
              </button>
            </li>
            <li>
              <button
                type="button"
                role="menuitem"
                data-testid={`${testId}-disable`}
                disabled={!canChangeAccess}
                className={cn(
                  dropdownMenuRowClassName,
                  "text-[var(--oh-danger)] hover:text-[var(--oh-danger)]",
                )}
                onClick={() => {
                  onDisableSelected();
                  close();
                }}
              >
                {t(I18nKey.INTEGRATIONS_HUB$DISABLE_SELECTED)}
              </button>
            </li>
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function ToolAccessRow({
  tool,
  mode,
  selected,
  showUsage,
  onToggleSelected,
  onChangeMode,
}: {
  tool: HubTool;
  mode: HubToolAccessMode;
  selected: boolean;
  showUsage: boolean;
  onToggleSelected: (selected: boolean) => void;
  onChangeMode: (mode: HubToolAccessMode) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const detailsId = `tool-details-${tool.name}`;
  const description =
    tool.description || t(I18nKey.INTEGRATIONS_HUB$TOOL_NO_DESCRIPTION);
  const lastUsed = tool.lastUsedAt ? new Date(tool.lastUsedAt) : null;
  const lastUsedLabel =
    lastUsed && !Number.isNaN(lastUsed.getTime())
      ? lastUsed.toLocaleString()
      : null;

  return (
    <div
      className={cn("px-3 py-2.5", selected && "bg-[rgba(255,255,255,0.03)]")}
      data-tool-name={tool.name}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <input
            type="checkbox"
            className="mt-1 h-3.5 w-3.5 shrink-0 accent-[var(--oh-accent)]"
            data-testid={`select-tool-${tool.name}`}
            aria-label={t(I18nKey.INTEGRATIONS_HUB$SELECT_TOOL, {
              name: tool.name,
            })}
            checked={selected}
            onChange={(event) => onToggleSelected(event.target.checked)}
          />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium leading-5 text-white">
              {tool.name}
            </p>
            <HubTruncatedText
              text={description}
              className="mt-0.5 text-xs leading-4 text-[var(--oh-text-secondary)]"
            />
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1 self-start">
          <AccessModeDropdown
            toolName={tool.name}
            mode={mode}
            maxMode={tool.maxAccessMode}
            onChange={onChangeMode}
          />
          <ToolDetailsCaret
            expanded={expanded}
            controlsId={detailsId}
            onToggle={() => setExpanded((current) => !current)}
          />
        </div>
      </div>
      {expanded ? (
        <div
          id={detailsId}
          data-testid={`tool-details-${tool.name}`}
          className="mt-2 ml-[26px] space-y-1 rounded-lg border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] px-3 py-2"
        >
          <p className="text-[11px] leading-4 text-[var(--oh-text-secondary)]">
            {t(I18nKey.INTEGRATIONS_HUB$DEFAULT_SCOPES_LABEL)}{" "}
            <span className="font-mono text-[11px] text-white/80">
              {tool.defaultScopes.length > 0
                ? tool.defaultScopes.join(", ")
                : t(I18nKey.INTEGRATIONS_HUB$NONE_LABEL)}
            </span>
          </p>
          {showUsage ? (
            <p
              className="text-[11px] leading-4 text-[var(--oh-text-secondary)]"
              data-testid={`tool-usage-${tool.name}`}
            >
              {lastUsedLabel
                ? t(I18nKey.INTEGRATIONS_HUB$LAST_TRACKED_INVOCATION, {
                    date: lastUsedLabel,
                  })
                : t(I18nKey.INTEGRATIONS_HUB$NO_TRACKED_USAGE)}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function HubToolAccessList({
  tools,
  searchTestId,
  showUsage = false,
  toolbarStart,
  toolbarEnd,
  onUpdateToolAccess,
}: HubToolAccessListProps) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [modeOverrides, setModeOverrides] = useState<
    Record<string, HubToolAccessMode>
  >({});
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const query = search.trim().toLowerCase();
  const visibleTools = useMemo(
    () => tools.filter((tool) => toolMatchesQuery(tool, query)),
    [query, tools],
  );
  const visibleNames = visibleTools.map((tool) => tool.name);
  const selectedVisibleNames = visibleNames.filter((name) => selected[name]);
  const selectedNames = Object.entries(selected)
    .filter(([, isSelected]) => isSelected)
    .map(([name]) => name);
  const allVisibleSelected =
    visibleNames.length > 0 &&
    selectedVisibleNames.length === visibleNames.length;

  const modeFor = (tool: HubTool) =>
    modeOverrides[tool.name] ?? tool.accessMode;

  const setToolMode = (toolName: string, mode: HubToolAccessMode) => {
    const tool = tools.find((item) => item.name === toolName);
    const nextMode = clampHubAccessMode(mode, tool?.maxAccessMode);
    setModeOverrides((current) => ({ ...current, [toolName]: nextMode }));
    onUpdateToolAccess?.(toolName, nextMode);
  };

  const applyBulkMode = (mode: HubToolAccessMode) => {
    selectedNames.forEach((name) => setToolMode(name, mode));
  };

  const setSelectedStateForTools = (names: string[], isSelected: boolean) => {
    setSelected((current) => {
      const next = { ...current };
      names.forEach((name) => {
        next[name] = isSelected;
      });
      return next;
    });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex items-center gap-2">
        {toolbarStart}
        <HubSearchField
          value={search}
          onChange={setSearch}
          placeholder={t(I18nKey.INTEGRATIONS_HUB$WIZARD_SEARCH_TOOLS)}
          testId={searchTestId}
        />
        <ToolAccessBulkMenu
          testId={`${searchTestId}-bulk-actions`}
          canSelectAll={visibleTools.length > 0 && !allVisibleSelected}
          canDeselect={selectedVisibleNames.length > 0}
          canChangeAccess={selectedNames.length > 0}
          onSelectAll={() => setSelectedStateForTools(visibleNames, true)}
          onDeselect={() => setSelectedStateForTools(visibleNames, false)}
          onEnableSelected={() => applyBulkMode("enabled")}
          onCaseByCaseSelected={() => applyBulkMode("approval")}
          onDisableSelected={() => applyBulkMode("disabled")}
        />
        {toolbarEnd}
      </div>
      <div className="min-h-0 max-h-[min(22rem,50vh)] flex-1 overflow-y-auto overflow-x-hidden rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)]">
        {tools.length === 0 || visibleTools.length === 0 ? (
          <p className="px-4 py-4 text-sm text-[var(--oh-text-secondary)]">
            {t(I18nKey.INTEGRATIONS_HUB$NO_TOOLS)}
          </p>
        ) : (
          <div className="divide-y divide-[var(--oh-border)]">
            {visibleTools.map((tool) => (
              <ToolAccessRow
                key={tool.name}
                tool={tool}
                mode={modeFor(tool)}
                selected={Boolean(selected[tool.name])}
                showUsage={showUsage}
                onToggleSelected={(isSelected) =>
                  setSelected((current) => ({
                    ...current,
                    [tool.name]: isSelected,
                  }))
                }
                onChangeMode={(mode) => setToolMode(tool.name, mode)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
