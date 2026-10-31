import { useTranslation } from "react-i18next";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";

export interface ProfileScopeItem {
  name: string;
  description?: string | null;
  /** Organization-shared entries get an "Organization" badge. */
  scope?: "personal" | "organization";
}

interface ProfileScopeListProps {
  /** Prefix: the list is `<testId>-list`, each row `<testId>-<name>`. */
  testId: string;
  items: ProfileScopeItem[];
  selected: string[];
  isDisabled?: boolean;
  onToggle: (name: string, checked: boolean) => void;
}

/**
 * Per-item toggles for a profile scope field such as `mcp_server_refs`.
 *
 * Rows are keyed by the wire name — the same identifier the API takes — so an
 * entry this build has no description for is still legible and selectable.
 */
export function ProfileScopeList({
  testId,
  items,
  selected,
  isDisabled = false,
  onToggle,
}: ProfileScopeListProps) {
  const { t } = useTranslation();
  return (
    <ul data-testid={`${testId}-list`} className="flex flex-col gap-2.5">
      {items.map(({ name, description, scope }) => (
        <li key={name} className="flex flex-col gap-0.5">
          <SettingsSwitch
            testId={`${testId}-${name}`}
            name={`${testId}-${name}`}
            isToggled={selected.includes(name)}
            isDisabled={isDisabled}
            onToggle={(checked) => onToggle(name, checked)}
          >
            <span className="font-mono">{name}</span>
            {scope === "organization" ? (
              <span
                data-testid={`${testId}-${name}-org-badge`}
                className="ml-2 inline-flex items-center rounded-full bg-blue-500/15 px-2 py-0.5 text-xs font-medium text-blue-300"
              >
                {t(I18nKey.SETTINGS$MARKETPLACE_SCOPE_ORG)}
              </span>
            ) : null}
          </SettingsSwitch>
          {description ? (
            <Typography.Text className="pl-11 text-xs text-tertiary-alt">
              {description}
            </Typography.Text>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
