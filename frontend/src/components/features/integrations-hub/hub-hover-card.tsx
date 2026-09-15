import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { cn } from "#/utils/utils";

const HOVER_CARD_OFFSET_PX = 8;
const HOVER_CARD_CLOSE_DELAY_MS = 80;

function getDisableAnimation(): boolean {
  return process.env.NODE_ENV === "test";
}

interface HubHoverCardProps {
  content: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
  testId?: string;
}

export function HubHoverCard({
  content,
  children,
  className,
  contentClassName,
  testId,
}: HubHoverCardProps) {
  const disableAnimation = getDisableAnimation();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const updatePosition = useCallback(() => {
    const node = triggerRef.current;
    if (!node) {
      return;
    }
    const rect = node.getBoundingClientRect();
    setPosition({
      top: rect.bottom + HOVER_CARD_OFFSET_PX,
      left: Math.min(rect.left, window.innerWidth - 280),
    });
  }, []);

  const show = useCallback(() => {
    clearCloseTimer();
    updatePosition();
    setOpen(true);
  }, [clearCloseTimer, updatePosition]);

  const hide = useCallback(() => {
    clearCloseTimer();
    if (disableAnimation) {
      setOpen(false);
      return;
    }
    closeTimerRef.current = setTimeout(() => {
      setOpen(false);
      closeTimerRef.current = null;
    }, HOVER_CARD_CLOSE_DELAY_MS);
  }, [clearCloseTimer, disableAnimation]);

  useEffect(() => clearCloseTimer, [clearCloseTimer]);

  return (
    <>
      <span
        ref={triggerRef}
        data-testid={testId}
        className={cn("inline-flex", className)}
        onMouseEnter={show}
        onMouseOver={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
      >
        {children}
      </span>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              role="tooltip"
              data-testid={testId ? `${testId}-content` : undefined}
              className={cn(
                "fixed z-[9999] max-w-xs rounded-lg border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] px-3 py-2 text-xs leading-5 text-white shadow-lg",
                contentClassName,
              )}
              style={{ top: position.top, left: position.left }}
              onMouseEnter={show}
              onMouseLeave={hide}
            >
              {content}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
