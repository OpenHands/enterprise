import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import { Ban, Check, ChevronDown, ListTodo } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { HubIntegrationModalHeader } from "#/components/features/integrations-hub/hub-integration-modal-header";
import { HubToolAccessList } from "#/components/features/integrations-hub/hub-tool-access-list";
import {
  HubModal,
  hubModalBodyClassName,
  hubModalFooterClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { allowedHubAccessModes } from "#/components/features/integrations-hub/hub-format";
import { I18nKey } from "#/i18n/declaration";
import type {
  HubIntegration,
  HubToolAccessMode,
} from "#/types/integrations-hub";
import {
  dropdownMenuListClassName,
  dropdownMenuPanelPaddingClassName,
  dropdownMenuRowClassName,
} from "#/utils/dropdown-classes";
import { cn } from "#/utils/utils";

const ACCESS_MODE_KEYS: Record<HubToolAccessMode, I18nKey> = {
  enabled: I18nKey.INTEGRATIONS_HUB$ACCESS_ENABLED,
  approval: I18nKey.INTEGRATIONS_HUB$ACCESS_APPROVAL,
  disabled: I18nKey.INTEGRATIONS_HUB$ACCESS_DISABLED,
};
const ACCESS_TRIGGER_CLASS: Record<HubToolAccessMode, string> = {
  enabled:
    "border-[color:rgba(165,231,94,0.3)] bg-[color:rgba(165,231,94,0.12)] text-[var(--oh-color-success)] hover:bg-[color:rgba(165,231,94,0.18)]",
  approval:
    "border-[color:rgba(217,181,90,0.28)] bg-[color:rgba(217,181,90,0.12)] text-[color:rgb(217,181,90)] hover:bg-[color:rgba(217,181,90,0.18)]",
  disabled:
    "border-[color:rgba(231,106,94,0.3)] bg-[color:rgba(231,106,94,0.12)] text-[var(--oh-color-danger)] hover:bg-[color:rgba(231,106,94,0.18)]",
};

const ACCESS_ICONS = {
  enabled: Check,
  approval: ListTodo,
  disabled: Ban,
} as const;

export function AccessModeDropdown({
  toolName,
  mode,
  maxMode,
  onChange,
}: {
  toolName: string;
  mode: HubToolAccessMode;
  maxMode?: HubToolAccessMode;
  onChange: (mode: HubToolAccessMode) => void;
}) {
  const { t } = useTranslation();
  const allowedModes = allowedHubAccessModes(maxMode);
  const [open, setOpen] = useState(false);
  const [portalStyle, setPortalStyle] = useState<CSSProperties>();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const Icon = ACCESS_ICONS[mode];

  useLayoutEffect(() => {
    if (!open) {
      return undefined;
    }

    const updatePosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) {
        return;
      }
      setPortalStyle({
        position: "fixed",
        zIndex: 9999,
        top: rect.bottom + 4,
        right: window.innerWidth - rect.right,
        minWidth: Math.max(rect.width, 160),
      });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const isInside = (target: EventTarget | null) => {
      if (!(target instanceof Node)) {
        return false;
      }
      return Boolean(
        menuRef.current?.contains(target) ||
        triggerRef.current?.contains(target),
      );
    };

    const handlePointerDownOutside = (event: Event) => {
      if (isInside(event.target)) {
        return;
      }
      setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDownOutside, true);
    document.addEventListener("touchstart", handlePointerDownOutside, true);
    window.addEventListener("keydown", handleKeyDown, true);
    return () => {
      document.removeEventListener("mousedown", handlePointerDownOutside, true);
      document.removeEventListener(
        "touchstart",
        handlePointerDownOutside,
        true,
      );
      window.removeEventListener("keydown", handleKeyDown, true);
    };
  }, [open]);

  return (
    <div
      className="relative w-fit shrink-0"
      data-access-mode={mode}
      data-max-access-mode={maxMode}
    >
      <button
        ref={triggerRef}
        type="button"
        data-testid={`access-mode-${toolName}`}
        aria-label={`Default access for ${toolName}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "inline-flex h-7 w-fit cursor-pointer items-center gap-1 rounded-md border px-2 text-[10px] font-semibold leading-4",
          ACCESS_TRIGGER_CLASS[mode],
        )}
        onClick={() => setOpen((current) => !current)}
      >
        <Icon className="size-3" aria-hidden strokeWidth={2} />
        {t(ACCESS_MODE_KEYS[mode])}
        <ChevronDown className="size-3" aria-hidden />
      </button>
      {open && portalStyle && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={menuRef}
              role="listbox"
              data-testid={`access-mode-${toolName}-panel`}
              style={portalStyle}
              className={cn(
                "rounded-[6px] bg-tertiary context-menu-box-shadow",
                dropdownMenuPanelPaddingClassName,
                dropdownMenuListClassName,
              )}
            >
              {allowedModes.map((nextMode) => {
                const ModeIcon = ACCESS_ICONS[nextMode];
                return (
                  <button
                    key={nextMode}
                    type="button"
                    role="option"
                    aria-selected={mode === nextMode}
                    className={dropdownMenuRowClassName}
                    onClick={() => {
                      onChange(nextMode);
                      setOpen(false);
                    }}
                  >
                    <ModeIcon
                      className="size-3.5"
                      aria-hidden
                      strokeWidth={2}
                    />
                    {t(ACCESS_MODE_KEYS[nextMode])}
                  </button>
                );
              })}
            </div>,
            document.getElementById("portal-root") || document.body,
          )
        : null}
    </div>
  );
}

export function HubIntegrationEnableRow({
  integration,
  onToggleEnabled,
}: {
  integration: HubIntegration;
  onToggleEnabled: () => void;
}) {
  const { t } = useTranslation();

  return (
    <div
      data-testid={`integration-detail-modal-enable-row-${integration.slug}`}
      className="mt-4 flex w-full items-center rounded-lg border border-[var(--oh-border)] bg-[rgba(255,255,255,0.04)] px-3 py-2.5"
    >
      <SettingsSwitch
        testId={`integration-detail-modal-toggle-${integration.slug}`}
        isToggled={integration.enabled}
        togglePosition="right"
        onToggle={onToggleEnabled}
      >
        {integration.enabled
          ? t(I18nKey.INTEGRATIONS_HUB$ENABLED_STATE)
          : t(I18nKey.INTEGRATIONS_HUB$DISABLED_STATE)}
      </SettingsSwitch>
    </div>
  );
}

interface IntegrationDetailModalProps {
  integration: HubIntegration;
  hideDisabledTools?: boolean;
  onClose: () => void;
  onToggleEnabled: () => void;
  onDelete: () => void;
  onUpdateToolAccess: (toolName: string, mode: HubToolAccessMode) => void;
}

export function IntegrationDetailModal({
  integration,
  hideDisabledTools = false,
  onClose,
  onToggleEnabled,
  onDelete,
  onUpdateToolAccess,
}: IntegrationDetailModalProps) {
  const { t } = useTranslation();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const visibleTools = hideDisabledTools
    ? integration.tools.filter((tool) => tool.accessMode !== "disabled")
    : integration.tools;

  return (
    <>
      <HubModal
        ariaLabel={integration.name}
        testId={`integration-detail-modal-${integration.slug}`}
        width="xl"
        className="gap-0"
        onClose={onClose}
      >
        <div className={hubModalBodyClassName}>
          <HubIntegrationModalHeader
            integration={integration}
            showDescription
          />

          <HubIntegrationEnableRow
            integration={integration}
            onToggleEnabled={onToggleEnabled}
          />

          <div className="mt-6">
            <HubToolAccessList
              tools={visibleTools}
              searchTestId={`integration-detail-tools-search-${integration.slug}`}
              showUsage
              onUpdateToolAccess={onUpdateToolAccess}
            />
          </div>
        </div>
        <div className={hubModalFooterClassName}>
          <BrandButton
            type="button"
            variant="danger"
            testId={`integration-detail-modal-delete-${integration.slug}`}
            onClick={() => setConfirmDelete(true)}
          >
            {t(I18nKey.INTEGRATIONS_HUB$DELETE)}
          </BrandButton>
          <BrandButton type="button" variant="secondary" onClick={onClose}>
            {t(I18nKey.INTEGRATIONS_HUB$CLOSE)}
          </BrandButton>
        </div>
      </HubModal>

      {confirmDelete ? (
        <HubModal
          ariaLabel={t(I18nKey.INTEGRATIONS_HUB$DELETE_TITLE)}
          testId="delete-integration-modal"
          onClose={() => setConfirmDelete(false)}
        >
          <div className={hubModalBodyClassName}>
            <h2 className="pr-8 text-base font-semibold text-white">
              {t(I18nKey.INTEGRATIONS_HUB$DELETE_TITLE)}
            </h2>
            <p className="mt-2 text-sm text-tertiary-light">
              {t(I18nKey.INTEGRATIONS_HUB$DELETE_BODY, {
                name: integration.name,
              })}
            </p>
          </div>
          <div className={hubModalFooterClassName}>
            <BrandButton
              type="button"
              variant="secondary"
              onClick={() => setConfirmDelete(false)}
            >
              {t(I18nKey.BUTTON$CANCEL)}
            </BrandButton>
            <BrandButton type="button" variant="danger" onClick={onDelete}>
              {t(I18nKey.INTEGRATIONS_HUB$DELETE)}
            </BrandButton>
          </div>
        </HubModal>
      ) : null}
    </>
  );
}
