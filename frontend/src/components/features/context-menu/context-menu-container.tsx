import React from "react";
import { cn } from "#/utils/utils";
import { useClickOutsideElement } from "#/hooks/use-click-outside-element";

interface ContextMenuContainerProps {
  children: React.ReactNode;
  onClose: () => void;
  testId?: string;
  className?: string;
}

export function ContextMenuContainer({
  children,
  onClose,
  testId,
  className,
}: ContextMenuContainerProps) {
  // Children rendered through a portal (e.g. the org selector's dropdown
  // menu) bubble clicks through the React tree but not the DOM tree, so the
  // document-level outside-click check would otherwise close the menu.
  const lastInsideClick = React.useRef<Event | null>(null);
  const ref = useClickOutsideElement<HTMLDivElement>((event) => {
    if (event === lastInsideClick.current) {
      return;
    }
    onClose();
  });

  return (
    <div
      ref={ref}
      data-testid={testId}
      onClickCapture={(event) => {
        lastInsideClick.current = event.nativeEvent;
      }}
      className={cn(
        // Base styling - same for ALL modes (SaaS, OSS, mobile, desktop)
        "absolute rounded-[12px] p-[25px]",
        "bg-surface-deep border border-[var(--oh-border-subtle)]",
        "text-white overflow-hidden z-[9999]",
        "context-menu-box-shadow",
        // Positioning
        "right-0 md:right-auto md:left-full md:bottom-0",
        "w-fit",
        className,
      )}
    >
      <div className="flex flex-row gap-4 items-stretch">{children}</div>
    </div>
  );
}
