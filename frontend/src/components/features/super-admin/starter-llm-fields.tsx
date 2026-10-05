import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { useConfig } from "#/hooks/query/use-config";
import { useProviderModels } from "#/hooks/query/use-provider-models";
import { useSearchProviders } from "#/hooks/query/use-search-providers";
import { I18nKey } from "#/i18n/declaration";
import { ComboboxCaretInline } from "#/ui/combobox-caret";
import { formControlFieldClassName } from "#/utils/form-control-classes";
import { mapProvider } from "#/utils/map-provider";
import { cn } from "#/utils/utils";

type ModelOption = { key: string; label: string };

export interface StarterLlmSelection {
  provider: string | null;
  model: string | null;
  apiKey: string;
}

/**
 * Single-select model menu. The closed control matches the provider dropdown;
 * the open panel lists one radio per model.
 */
function ModelSelect({
  models,
  selected,
  disabled,
  placeholder,
  label,
  onSelect,
}: {
  models: readonly ModelOption[];
  selected: string | null;
  disabled: boolean;
  placeholder: string;
  label: string;
  onSelect: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>();
  const summary = models.find((item) => item.key === selected)?.label;

  useLayoutEffect(() => {
    if (!open) {
      return undefined;
    }
    const update = () => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) {
        return;
      }
      setMenuStyle({
        zIndex: 80,
        left: rect.left,
        width: rect.width,
        top: rect.bottom + 4,
      });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (
        anchorRef.current?.contains(target) ||
        menuRef.current?.contains(target)
      ) {
        return;
      }
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);

  return (
    <div className="flex flex-col gap-2.5">
      <span className="text-sm text-white">{label}</span>
      <div ref={anchorRef} className="relative w-full">
        <button
          type="button"
          data-testid="sa-nux-llm-model"
          aria-haspopup="listbox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            formControlFieldClassName,
            "flex cursor-pointer items-center justify-between gap-2 text-left",
            disabled && "cursor-not-allowed",
          )}
          onClick={() => setOpen((current) => !current)}
        >
          <span
            className={cn(
              "min-w-0 flex-1 truncate",
              summary ? "text-white" : "text-tertiary-alt",
            )}
          >
            {summary || placeholder}
          </span>
          <ComboboxCaretInline isOpen={open} />
        </button>
      </div>
      {open
        ? createPortal(
            <div
              ref={menuRef}
              data-testid="sa-nux-llm-models"
              className="fixed overflow-hidden rounded-xl border border-[var(--oh-border)] bg-content1 text-white shadow-lg"
              style={menuStyle}
            >
              <ul
                role="listbox"
                aria-label={label}
                className="max-h-64 overflow-y-auto"
              >
                {models.map((item) => {
                  const checked = item.key === selected;
                  return (
                    <li key={item.key} role="option" aria-selected={checked}>
                      <label className="flex cursor-pointer items-center gap-3 px-3 py-2.5 text-sm text-white hover:bg-white/5">
                        <input
                          type="radio"
                          className="h-4 w-4 accent-[#FFFF8B]"
                          checked={checked}
                          onChange={() => onSelect(item.key)}
                          // Click, not change, so picking the current model also closes the menu.
                          onClick={() => setOpen(false)}
                        />
                        <span>{item.label}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </div>,
            anchorRef.current?.closest("[role='dialog']") ?? document.body,
          )
        : null}
    </div>
  );
}

/**
 * The first LLM settings controls, so a new install can pick a provider
 * without leaving the starter modal.
 */
export function StarterLlmFields({
  value,
  onChange,
}: {
  value: StarterLlmSelection;
  onChange: (next: StarterLlmSelection) => void;
}) {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { data: providers = [] } = useSearchProviders();
  const { data: providerModels = [] } = useProviderModels(value.provider);
  // Same list as the org LLM form's ModelSelector: hidden aliases are left
  // out and verified models come first.
  const visibleModels = providerModels.filter((item) => !item.hidden);
  const models = [
    ...visibleModels.filter((item) => item.verified),
    ...visibleModels.filter((item) => !item.verified),
  ].map((item) => ({ key: item.name, label: item.name }));
  // Same rule as the org LLM form: no key for OpenHands models, or when the
  // install turns off user LLM configuration.
  const showApiKey =
    !!value.provider &&
    value.provider !== "openhands" &&
    config?.feature_flags?.allow_user_llm_configuration !== false;

  return (
    <div
      className="flex flex-col gap-3"
      data-testid="sa-nux-starter-llm-fields"
    >
      <SettingsDropdownInput
        testId="sa-nux-llm-provider"
        name="provider"
        label={t(I18nKey.LLM$PROVIDER)}
        placeholder={t(I18nKey.LLM$SELECT_PROVIDER_PLACEHOLDER)}
        items={providers.map((item) => ({
          key: item.name,
          label: mapProvider(item.name),
        }))}
        selectedKey={value.provider}
        onSelectionChange={(key) => {
          // A key typed for another provider is never sent with this one.
          onChange({
            provider: key == null ? null : String(key),
            model: null,
            apiKey: "",
          });
        }}
      />
      <ModelSelect
        label={t(I18nKey.LLM$MODEL)}
        placeholder={t(I18nKey.LLM$SELECT_MODEL_PLACEHOLDER)}
        models={models}
        selected={value.model}
        disabled={models.length === 0}
        onSelect={(model) => onChange({ ...value, model })}
      />
      {showApiKey ? (
        <SettingsInput
          testId="sa-nux-llm-api-key"
          label={t(I18nKey.SETTINGS_FORM$API_KEY)}
          type="password"
          value={value.apiKey}
          onChange={(apiKey) => onChange({ ...value, apiKey })}
          autoComplete="off"
        />
      ) : null}
    </div>
  );
}
