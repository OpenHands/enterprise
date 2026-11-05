import React, { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";
import { SkillWithState } from "#/types/settings";
import { Toggle } from "#/components/shared/toggle/toggle";
import { InfoTooltip } from "#/components/features/settings/info-tooltip";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { SearchIcon } from "#/components/shared/icons/inline-icons";
import { I18nKey } from "#/i18n/declaration";
import { HorizontalScrollFade } from "#/components/shared/horizontal-scroll-fade";
import { cn } from "#/utils/utils";
import {
  formControlInlineInputClassName,
  formControlShellClassName,
} from "#/utils/form-control-classes";
import {
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
  settingsListTableMinWidthStyle,
} from "#/utils/settings-list-classes";

interface SkillsTableProps {
  skills: SkillWithState[];
  onToggle: (skillId: string) => void;
  typeOptions: { key: string; label: string }[];
  repositoryOptions: { key: string; label: string }[];
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onTypeChange: (type: string | null) => void;
  onRepositoryChange: (repo: string | null) => void;
}

function ScopeBadge({ scope }: { scope: "instance" | "org" | "personal" }) {
  const { t } = useTranslation();
  const label = {
    instance: t(I18nKey.SETTINGS$MARKETPLACE_SCOPE_INSTANCE),
    org: t(I18nKey.SETTINGS$MARKETPLACE_SCOPE_ORG),
    personal: t(I18nKey.SETTINGS$MARKETPLACE_SCOPE_PERSONAL),
  }[scope];

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        scope === "instance" && "bg-tertiary/60 text-tertiary-light",
        scope === "org" && "bg-blue-500/15 text-blue-300",
        scope === "personal" && "bg-success/15 text-success",
      )}
    >
      {label}
    </span>
  );
}

const HEADER_CLASS = settingsListTableHeaderCellClassName;
const CELL_CLASS = "px-4 py-3 align-middle";

export function SkillsTable({
  skills,
  onToggle,
  typeOptions,
  repositoryOptions,
  searchQuery,
  onSearchChange,
  onTypeChange,
  onRepositoryChange,
}: SkillsTableProps) {
  const { t } = useTranslation();
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());

  const toggleExpanded = useCallback((skillId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(skillId)) {
        next.delete(skillId);
      } else {
        next.add(skillId);
      }
      return next;
    });
  }, []);

  return (
    <div className="flex flex-col gap-4">
      {/* Filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className={cn(formControlShellClassName, "flex-1")}>
          <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
            <SearchIcon />
          </span>
          <input
            data-testid="search-skills-input"
            type="text"
            placeholder={t(I18nKey.SETTINGS$SEARCH_PLACEHOLDER)}
            aria-label={t(I18nKey.SETTINGS$SEARCH_PLACEHOLDER)}
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className={cn(formControlInlineInputClassName, "text-white")}
          />
        </div>
        <div className="w-full sm:w-52">
          <SettingsDropdownInput
            testId="type-filter-dropdown"
            name="type-filter"
            items={typeOptions}
            defaultSelectedKey="all"
            onSelectionChange={(key) => onTypeChange(key?.toString() ?? null)}
            placeholder={t(I18nKey.SETTINGS$ALL_TYPES)}
          />
        </div>
        <div className="w-full sm:w-52">
          <SettingsDropdownInput
            testId="repository-filter-dropdown"
            name="repository-filter"
            items={repositoryOptions}
            defaultSelectedKey="all"
            onSelectionChange={(key) =>
              onRepositoryChange(key?.toString() ?? null)
            }
            placeholder={t(I18nKey.SETTINGS$ALL_REPOSITORIES)}
          />
        </div>
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-xl border border-[var(--oh-border)] bg-base-secondary/20 table-box-shadow">
        <HorizontalScrollFade>
          <table
            className="w-full table-fixed border-collapse text-sm"
            style={settingsListTableMinWidthStyle(5)}
          >
            <colgroup>
              <col className="w-[28%]" />
              {/* Source takes whatever width the sized columns leave. */}
              <col />
              <col className="w-[16%]" />
              <col className="w-[16%]" />
              {/* Fixed so the "Enabled" header plus its info icon never
                  overflows the last cell (a 10% column was ~1px short). */}
              <col className="w-28" />
            </colgroup>
            <thead className={settingsListTableHeadClassName}>
              <tr>
                <th className={HEADER_CLASS}>{t(I18nKey.SETTINGS$NAME)}</th>
                <th className={HEADER_CLASS}>
                  {t(I18nKey.SETTINGS$MARKETPLACE_SOURCE)}
                </th>
                <th className={HEADER_CLASS}>{t(I18nKey.SETTINGS$TYPE)}</th>
                <th className={HEADER_CLASS}>
                  {t(I18nKey.SETTINGS$MARKETPLACE_SCOPE_LABEL)}
                </th>
                <th className={HEADER_CLASS}>
                  <span className="inline-flex items-center gap-1.5">
                    {t(I18nKey.SETTINGS$ENABLED)}
                    <InfoTooltip
                      content={t(I18nKey.SETTINGS$ENABLED_TOOLTIP)}
                    />
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {skills.map((skill) => {
                const isExpanded = expandedIds.has(skill.id);
                return (
                  <React.Fragment key={skill.id}>
                    <tr
                      data-testid={`skill-row-${skill.id}`}
                      onClick={() => toggleExpanded(skill.id)}
                      className={cn(
                        "cursor-pointer border-t border-[var(--oh-border)] transition-colors hover:bg-base-secondary/40",
                        isExpanded && "bg-base-secondary/30",
                      )}
                    >
                      <td
                        className={cn(CELL_CLASS, "font-medium text-content-2")}
                        title={skill.name}
                      >
                        <span className="flex min-w-0 items-center gap-1.5">
                          <button
                            type="button"
                            aria-expanded={isExpanded}
                            aria-label={t(
                              I18nKey.SETTINGS$SKILL_SHOW_DESCRIPTION,
                              {
                                name: skill.name,
                              },
                            )}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleExpanded(skill.id);
                            }}
                            className="shrink-0 rounded text-tertiary-alt hover:text-content-2"
                          >
                            <ChevronRight
                              aria-hidden
                              className={cn(
                                "h-4 w-4 transition-transform",
                                isExpanded && "rotate-90",
                              )}
                            />
                          </button>
                          <span className="block truncate">{skill.name}</span>
                        </span>
                      </td>
                      <td className={cn(CELL_CLASS, "text-tertiary-alt")}>
                        <span
                          className="block truncate"
                          title={skill.repository}
                        >
                          {skill.marketplaceName ?? skill.repository}
                        </span>
                      </td>
                      <td className={CELL_CLASS}>
                        <span className="inline-flex items-center rounded-full bg-tertiary/60 px-2.5 py-0.5 text-xs font-medium text-tertiary-light">
                          {skill.type}
                        </span>
                      </td>
                      <td className={CELL_CLASS}>
                        <ScopeBadge scope={skill.scope} />
                      </td>
                      <td
                        className={CELL_CLASS}
                        onClick={(e) => e.stopPropagation()}
                      >
                        {skill.type === "plugin" ? (
                          // Plugins are enabled via their marketplace's Auto-Load
                          // toggle, so this reflects that state read-only.
                          <Toggle
                            checked={skill.isAutoLoad}
                            disabled
                            title={t(I18nKey.SETTINGS$PLUGIN_AUTOLOAD_READONLY)}
                            aria-label={`Auto-load state for ${skill.name}`}
                          />
                        ) : (
                          <Toggle
                            checked={skill.isEnabled}
                            onClick={() => onToggle(skill.id)}
                            aria-label={`Toggle enabled for ${skill.name}`}
                          />
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr data-testid={`skill-description-${skill.id}`}>
                        <td
                          colSpan={5}
                          className="bg-base-secondary/30 px-4 pb-4 pt-0"
                        >
                          <p className="whitespace-pre-wrap break-words pl-[22px] text-sm text-tertiary-alt">
                            {skill.description ||
                              t(I18nKey.SETTINGS$SKILL_NO_DESCRIPTION)}
                          </p>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
              {skills.length === 0 && (
                <tr className="border-t border-[var(--oh-border)]">
                  <td
                    colSpan={5}
                    className="px-4 py-10 text-center text-sm text-tertiary-alt"
                  >
                    {t(I18nKey.SETTINGS$NO_SKILLS_MATCH_FILTERS)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </HorizontalScrollFade>
      </div>
    </div>
  );
}
