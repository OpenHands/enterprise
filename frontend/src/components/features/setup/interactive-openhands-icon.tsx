import { useEffect, useRef } from "react";
import OpenHandsFavicon from "#/assets/branding/openhands-favicon.svg?react";
import { cn } from "#/utils/utils";
import "./interactive-openhands-icon.css";

interface InteractiveOpenHandsIconProps {
  className?: string;
  /** Accessible label for the icon (decorative when omitted). */
  label?: string;
}

/**
 * App-icon treatment from the OpenHands Electron landing page:
 * pointer-driven 3D tilt + orbiting background glow.
 */
export function InteractiveOpenHandsIcon({
  className,
  label,
}: InteractiveOpenHandsIconProps) {
  const iconRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const updateFromPointer = (clientX: number, clientY: number) => {
      const target = iconRef.current;
      if (!target) {
        return;
      }

      const rect = target.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const deltaX = clientX - centerX;
      const deltaY = clientY - centerY;

      const angleRadians = Math.atan2(deltaY, deltaX);
      const angleDegrees = (angleRadians * 180) / Math.PI + 90;
      target.style.setProperty(
        "--pointer-angle",
        `${(angleDegrees + 360) % 360}deg`,
      );

      const normalizedX = deltaX / (rect.width / 2);
      const normalizedY = deltaY / (rect.height / 2);
      const rotateY = Math.max(-1, Math.min(1, normalizedX)) * 10;
      const rotateX = Math.max(-1, Math.min(1, -normalizedY)) * 10;

      target.style.setProperty("--tilt-x", `${rotateX.toFixed(2)}deg`);
      target.style.setProperty("--tilt-y", `${rotateY.toFixed(2)}deg`);
    };

    const handlePointerMove = (event: PointerEvent) => {
      updateFromPointer(event.clientX, event.clientY);
    };

    window.addEventListener("pointermove", handlePointerMove);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
    };
  }, []);

  return (
    <div
      className={cn("oh-interactive-icon-stage", className)}
      data-testid="interactive-openhands-icon"
    >
      <div className="oh-interactive-icon-glow" aria-hidden />
      <div
        ref={iconRef}
        className="oh-interactive-icon"
        aria-hidden={label ? undefined : true}
        aria-label={label}
        role={label ? "img" : undefined}
      >
        <OpenHandsFavicon className="oh-interactive-icon__svg" />
      </div>
    </div>
  );
}
