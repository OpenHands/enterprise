import { cn } from "#/utils/utils";
import { formControlButtonClassName } from "#/utils/form-control-classes";

interface BrandButtonProps {
  testId?: string;
  name?: string;
  variant: "primary" | "secondary" | "danger" | "ghost-danger";
  type: React.ButtonHTMLAttributes<HTMLButtonElement>["type"];
  isDisabled?: boolean;
  className?: string;
  onClick?: (event?: React.MouseEvent<HTMLButtonElement>) => void;
  startContent?: React.ReactNode;
}

export function BrandButton({
  testId,
  name,
  children,
  variant,
  type,
  isDisabled,
  className,
  onClick,
  startContent,
}: React.PropsWithChildren<BrandButtonProps>) {
  return (
    <button
      name={name}
      data-testid={testId}
      disabled={isDisabled}
      // The type is already passed as a prop to the button component
      // eslint-disable-next-line react/button-has-type
      type={type}
      onClick={onClick}
      className={cn(
        formControlButtonClassName,
        variant === "primary" &&
          "bg-primary text-[var(--oh-color-base)] hover:opacity-80 disabled:bg-[var(--oh-interactive-hover)] disabled:text-[var(--oh-muted)] disabled:opacity-100",
        variant === "secondary" &&
          "border border-[var(--oh-border)] bg-base-secondary text-white hover:bg-surface-raised",
        variant === "danger" && "bg-red-600 text-white hover:bg-red-700",
        variant === "ghost-danger" &&
          "h-auto min-h-0 bg-transparent px-0 text-red-600 underline hover:text-red-700 hover:no-underline font-normal",
        startContent && "flex items-center justify-center gap-2",
        className,
      )}
    >
      {startContent}
      {children}
    </button>
  );
}
