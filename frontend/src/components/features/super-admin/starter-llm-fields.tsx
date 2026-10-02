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
import { I18nKey } from "#/i18n/declaration";
import { ComboboxCaretInline } from "#/ui/combobox-caret";
import { formControlFieldClassName } from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

const PROVIDERS = [
  {
    key: "openai",
    label: "OpenAI",
    models: [
      { key: "gpt-4o", label: "GPT-4o" },
      { key: "gpt-4o-mini", label: "GPT-4o mini" },
    ],
  },
  {
    key: "anthropic",
    label: "Anthropic",
    models: [
      { key: "claude-sonnet-4-5-20250929", label: "Claude Sonnet 4.5" },
      { key: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" },
    ],
  },
  {
    key: "openhands",
    label: "OpenHands",
    models: [
      { key: "claude-sonnet-4-5-20250929", label: "Claude Sonnet 4.5" },
      { key: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" },
    ],
  },
] as const;

type ModelOption = { key: string; label: string };

/**
 * Multi-select model menu. The closed control matches the provider dropdown;
 * the open panel keeps the checkboxes and All action.
 */
function ModelSelect({
  models,
  selected,
  disabled,
  placeholder,
  allLabel,
  label,
  onToggle,
  onSelectAll,
}: {
  models: readonly ModelOption[];
  selected: string[];
  disabled: boolean;
  placeholder: string;
  allLabel: string;
  label: string;
  onToggle: (key: string) => void;
  onSelectAll: () => void;
}) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuStyle, setMenuStyle] = useState<CSSProperties>();
  const summary = models
    .filter((item) => selected.includes(item.key))
    .map((item) => item.label)
    .join(", ");

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
              <div className="flex items-center justify-end border-b border-[var(--oh-border)] px-2 py-1.5">
                <button
                  type="button"
                  data-testid="sa-nux-llm-model-all"
                  className="rounded-md border border-[var(--oh-border)] bg-transparent px-2 py-0.5 text-xs text-white hover:border-white"
                  onClick={onSelectAll}
                >
                  {allLabel}
                </button>
              </div>
              <ul role="listbox" aria-label={label} aria-multiselectable="true">
                {models.map((item) => {
                  const checked = selected.includes(item.key);
                  return (
                    <li key={item.key} role="option" aria-selected={checked}>
                      <label className="flex cursor-pointer items-center gap-3 px-3 py-2.5 text-sm text-white hover:bg-white/5">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-[#FFFF8B]"
                          checked={checked}
                          onChange={() => onToggle(item.key)}
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
export function StarterLlmFields() {
  const { t } = useTranslation();
  const [provider, setProvider] = useState<string | null>(null);
  const [modelsSelected, setModelsSelected] = useState<string[]>([]);
  const [apiKey, setApiKey] = useState("");
  const models = PROVIDERS.find((item) => item.key === provider)?.models ?? [];
  const allSelected =
    models.length > 0 && models.every((item) => modelsSelected.includes(item.key));

  const toggleModel = (key: string) => {
    setModelsSelected((current) =>
      current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key],
    );
  };

  const selectAllModels = () => {
    setModelsSelected(allSelected ? [] : models.map((item) => item.key));
  };

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
        items={PROVIDERS.map(({ key, label }) => ({ key, label }))}
        selectedKey={provider}
        onSelectionChange={(key) => {
          setProvider(key == null ? null : String(key));
          setModelsSelected([]);
        }}
      />
      <ModelSelect
        label={t(I18nKey.LLM$MODEL)}
        placeholder={t(I18nKey.LLM$SELECT_MODEL_PLACEHOLDER)}
        allLabel={t(I18nKey.SETTINGS$ALL)}
        models={models}
        selected={modelsSelected}
        disabled={!provider}
        onToggle={toggleModel}
        onSelectAll={selectAllModels}
      />
      {provider && provider !== "openhands" ? (
        <SettingsInput
          testId="sa-nux-llm-api-key"
          label={t(I18nKey.SETTINGS_FORM$API_KEY)}
          type="password"
          value={apiKey}
          onChange={setApiKey}
          autoComplete="off"
        />
      ) : null}
    </div>
  );
}
