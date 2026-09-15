import { useLayoutEffect, useRef, useState } from "react";
import { Tooltip } from "@heroui/react";
import { cn } from "#/utils/utils";

interface HubTruncatedTextProps {
  text: string;
  className?: string;
  /** 1 = single-line ellipsis. Higher values use line-clamp. */
  lines?: 1 | 2 | 3;
}

const LINE_CLAMP_CLASS = {
  1: "truncate",
  2: "line-clamp-2",
  3: "line-clamp-3",
} as const;

export function HubTruncatedText({
  text,
  className,
  lines = 1,
}: HubTruncatedTextProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [overflows, setOverflows] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) {
      return undefined;
    }

    const measure = () => {
      const overflowing =
        lines === 1
          ? el.scrollWidth > el.clientWidth + 1
          : el.scrollHeight > el.clientHeight + 1;
      setOverflows(overflowing);
    };

    measure();
    if (typeof ResizeObserver === "undefined") {
      return undefined;
    }

    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [lines, text]);

  return (
    <Tooltip
      content={text}
      isDisabled={!overflows}
      placement="top"
      closeDelay={100}
      className="bg-white text-black"
    >
      <span
        ref={ref}
        className={cn("block min-w-0", LINE_CLAMP_CLASS[lines], className)}
      >
        {text}
      </span>
    </Tooltip>
  );
}
