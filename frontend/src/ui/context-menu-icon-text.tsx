import { cn } from "#/utils/utils";
import {
  dropdownMenuRowGapClassName,
  dropdownMenuRowIconWrapperClassName,
} from "#/utils/dropdown-classes";

interface ContextMenuIconTextProps {
  icon: React.ReactNode;
  text: string;
  rightIcon?: React.ReactNode;
  className?: string;
}

export function ContextMenuIconText({
  icon,
  text,
  rightIcon,
  className,
}: ContextMenuIconTextProps) {
  return (
    <div
      className={cn(
        "flex min-w-0 w-full items-center justify-between",
        dropdownMenuRowGapClassName,
        className,
      )}
    >
      <div
        className={cn("flex min-w-0 items-center", dropdownMenuRowGapClassName)}
      >
        <span className={dropdownMenuRowIconWrapperClassName} aria-hidden>
          {icon}
        </span>
        <span className="text-sm font-normal leading-5">{text}</span>
      </div>
      {rightIcon ? (
        <span className={dropdownMenuRowIconWrapperClassName} aria-hidden>
          {rightIcon}
        </span>
      ) : null}
    </div>
  );
}
