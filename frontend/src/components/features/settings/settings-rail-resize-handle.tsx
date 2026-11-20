import { useState } from "react";
import { cn } from "#/utils/utils";

interface SettingsRailResizeHandleProps {
  onMouseDown: (event: React.MouseEvent) => void;
  isDragging: boolean;
}

/** Drag edge on the settings rail's right border, styled like the agent-canvas sidebar handle. */
export function SettingsRailResizeHandle({
  onMouseDown,
  isDragging,
}: SettingsRailResizeHandleProps) {
  const [isHovering, setIsHovering] = useState(false);

  return (
    <div
      data-testid="settings-rail-resize-handle"
      className="absolute inset-y-0 -right-px z-10 w-0"
      aria-hidden
    >
      <div
        className="absolute inset-y-0 left-1/2 w-3 -translate-x-1/2 cursor-ew-resize"
        onMouseDown={onMouseDown}
        onMouseEnter={() => setIsHovering(true)}
        onMouseLeave={() => setIsHovering(false)}
      />
      <div
        className={cn(
          "pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors",
          isDragging || isHovering ? "bg-white" : "bg-transparent",
        )}
      />
    </div>
  );
}
