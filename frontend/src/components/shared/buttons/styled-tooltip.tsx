import { Tooltip } from "@heroui/react";
import React, { ReactNode } from "react";
import { cn } from "#/utils/utils";

export interface StyledTooltipProps {
  children: ReactNode;
  content: string | ReactNode;
  tooltipClassName?: React.HTMLAttributes<HTMLDivElement>["className"];
  placement?: "top" | "bottom" | "left" | "right";
  showArrow?: boolean;
  closeDelay?: number;
}

export function StyledTooltip({
  children,
  content,
  tooltipClassName,
  placement = "right",
  showArrow = false,
  closeDelay = 100,
}: StyledTooltipProps) {
  return (
    <Tooltip closeDelay={closeDelay} delay={0}>
      <Tooltip.Trigger className="inline-flex">{children}</Tooltip.Trigger>
      <Tooltip.Content
        placement={placement}
        showArrow={showArrow}
        className={cn("bg-white text-black", tooltipClassName)}
      >
        {showArrow ? <Tooltip.Arrow /> : null}
        {content}
      </Tooltip.Content>
    </Tooltip>
  );
}
