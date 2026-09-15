import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SearchIcon } from "#/components/shared/icons/inline-icons";
import { I18nKey } from "#/i18n/declaration";
import {
  formControlFocusWithinClassName,
  formControlHeightClassName,
  formControlRadiusClassName,
  formControlBorderClassName,
  formControlSurfaceClassName,
} from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

interface HubSearchFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  testId: string;
  className?: string;
}

export function HubSearchField({
  value,
  onChange,
  placeholder,
  testId,
  className,
}: HubSearchFieldProps) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        "relative flex min-w-0 shrink-0 items-center",
        formControlHeightClassName,
        formControlRadiusClassName,
        formControlBorderClassName,
        formControlSurfaceClassName,
        formControlFocusWithinClassName,
        className ?? "flex-1",
      )}
    >
      <span className="ml-3 shrink-0 text-tertiary-alt" aria-hidden>
        <SearchIcon />
      </span>
      <input
        data-testid={testId}
        type="search"
        placeholder={placeholder}
        aria-label={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 min-h-9 min-w-0 flex-1 border-0 bg-transparent px-3 py-0 text-sm leading-5 text-white outline-none placeholder:text-tertiary-alt [&::-webkit-search-cancel-button]:hidden"
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label={t(I18nKey.INTEGRATIONS_HUB$SEARCH_CLEAR)}
          data-testid={`${testId}-clear`}
          className="mr-2 cursor-pointer rounded p-1 text-tertiary-alt hover:text-white"
        >
          <X className="size-4" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}
