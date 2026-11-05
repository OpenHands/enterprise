import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { useLocalStorage } from "@uidotdev/usehooks";

/**
 * Same key, default and limits as the agent-canvas sidebar. Canvas and the
 * settings pages share an origin, so a width chosen in one is used by the other.
 */
export const SIDEBAR_WIDTH_STORAGE_KEY = "openhands-sidebar-width";
export const SIDEBAR_DEFAULT_WIDTH_PX = 300;
export const SIDEBAR_MIN_WIDTH_PX = 240;
export const SIDEBAR_MAX_WIDTH_PX = 560;

const DRAG_SHIELD_Z_INDEX = 200;

const clampWidth = (width: number) =>
  Math.max(SIDEBAR_MIN_WIDTH_PX, Math.min(SIDEBAR_MAX_WIDTH_PX, width));

/** Width of a left-anchored sidebar that follows the pointer while its edge is dragged. */
export function useResizableSidebarWidth(
  sidebarRef: RefObject<HTMLElement | null>,
) {
  const [persistedWidth, setPersistedWidth] = useLocalStorage<number>(
    SIDEBAR_WIDTH_STORAGE_KEY,
    SIDEBAR_DEFAULT_WIDTH_PX,
  );
  const [width, setWidth] = useState(() =>
    clampWidth(Number(persistedWidth) || SIDEBAR_DEFAULT_WIDTH_PX),
  );
  const [isDragging, setIsDragging] = useState(false);
  const widthRef = useRef(width);

  widthRef.current = width;

  const handleMouseDown = useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    setIsDragging(true);
  }, []);

  const handleMouseMove = useCallback(
    (event: MouseEvent) => {
      if (!sidebarRef.current) {
        return;
      }
      const { left } = sidebarRef.current.getBoundingClientRect();
      setWidth(clampWidth(event.clientX - left));
    },
    [sidebarRef],
  );

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
    setPersistedWidth(widthRef.current);
  }, [setPersistedWidth]);

  useLayoutEffect(() => {
    if (!isDragging) {
      return undefined;
    }

    // Keep the resize cursor and pointer events while the pointer crosses
    // iframes or other content during the drag.
    const shield = document.createElement("div");
    shield.setAttribute("aria-hidden", "true");
    Object.assign(shield.style, {
      position: "fixed",
      inset: "0",
      zIndex: String(DRAG_SHIELD_Z_INDEX),
      cursor: "ew-resize",
    });
    document.body.appendChild(shield);

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    document.body.style.cursor = "ew-resize";
    document.body.style.userSelect = "none";

    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      shield.remove();
    };
  }, [handleMouseMove, handleMouseUp, isDragging]);

  return { width, isDragging, handleMouseDown };
}
