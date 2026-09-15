import { useEffect, useLayoutEffect, useRef, useState } from "react";
import ReactDOM from "react-dom";
import { Check, type LucideIcon } from "lucide-react";
import { ContextMenuListItem } from "#/components/features/context-menu/context-menu-list-item";
import { ContextMenuIconText } from "#/ui/context-menu-icon-text";
import { ContextMenu } from "#/ui/context-menu";
import { cn } from "#/utils/utils";

export interface ViewModeOption<T extends string> {
  value: T;
  icon: LucideIcon;
  label: string;
  testId: string;
}

interface ViewModeToggleProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: ViewModeOption<T>[];
  ariaLabel: string;
  testId: string;
  disabled?: boolean;
}

export function ViewModeToggle<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  testId,
  disabled = false,
}: ViewModeToggleProps<T>) {
  const [open, setOpen] = useState(false);
  const [portalStyle, setPortalStyle] = useState<React.CSSProperties>();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);

  const activeOption =
    options.find((option) => option.value === value) ?? options[0]!;
  const ActiveIcon = activeOption.icon;

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return undefined;

    const updatePosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      if (!rect) return;

      const gap = 4;
      setPortalStyle({
        position: "fixed",
        zIndex: 9999,
        top: rect.bottom + gap,
        right: window.innerWidth - rect.right,
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
    if (!open) return undefined;

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        triggerRef.current?.contains(target) ||
        menuRef.current?.contains(target)
      ) {
        return;
      }
      setOpen(false);
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const menu =
    open && portalStyle ? (
      <ContextMenu ref={menuRef} theme="popover" className="min-w-[10rem]">
        {options.map((option) => {
          const Icon = option.icon;
          return (
            <li key={option.value}>
              <ContextMenuListItem
                testId={option.testId}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
                className="group"
              >
                <ContextMenuIconText
                  icon={<Icon />}
                  text={option.label}
                  rightIcon={
                    value === option.value ? (
                      <Check className="size-4" />
                    ) : undefined
                  }
                />
              </ContextMenuListItem>
            </li>
          );
        })}
      </ContextMenu>
    ) : null;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        data-testid={testId}
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-disabled={disabled}
        disabled={disabled}
        onClick={() => {
          if (disabled) return;
          setOpen((current) => !current);
        }}
        className={cn(
          "inline-flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-[var(--oh-border)] bg-base-secondary text-white transition-colors hover:bg-[var(--oh-interactive-hover)] focus-visible:border-white/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/20",
          "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-base-secondary",
        )}
      >
        <ActiveIcon className="size-4" aria-hidden />
      </button>

      {open && portalStyle && typeof document !== "undefined"
        ? ReactDOM.createPortal(
            <div style={portalStyle}>{menu}</div>,
            document.body,
          )
        : null}
    </>
  );
}
