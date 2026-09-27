import { ChevronLeft } from "#/assets/chevron-left";
import { ChevronRight } from "#/assets/chevron-right";
import { cn } from "#/utils/utils";

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  className?: string;
}

export function Pagination({
  currentPage,
  totalPages,
  onPageChange,
  className,
}: PaginationProps) {
  // Generate page numbers to display
  const getPageNumbers = (): (number | "ellipsis")[] => {
    const pages: (number | "ellipsis")[] = [];
    const showEllipsis = totalPages > 7;

    if (!showEllipsis) {
      // Show all pages if 7 or fewer
      for (let i = 1; i <= totalPages; i += 1) {
        pages.push(i);
      }
    } else {
      // Always show first page
      pages.push(1);

      if (currentPage > 3) {
        pages.push("ellipsis");
      }

      // Show pages around current
      const start = Math.max(2, currentPage - 1);
      const end = Math.min(totalPages - 1, currentPage + 1);

      for (let i = start; i <= end; i += 1) {
        pages.push(i);
      }

      if (currentPage < totalPages - 2) {
        pages.push("ellipsis");
      }

      // Always show last page
      if (totalPages > 1) {
        pages.push(totalPages);
      }
    }

    return pages;
  };

  const canGoPrevious = currentPage > 1;
  const canGoNext = currentPage < totalPages;

  if (totalPages <= 1) {
    return null;
  }

  return (
    <nav
      className={cn("flex items-center justify-center gap-1", className)}
      aria-label="Pagination"
    >
      {/* Previous button */}
      <button
        type="button"
        onClick={() => onPageChange(currentPage - 1)}
        disabled={!canGoPrevious}
        className={cn(
          "rounded-md p-2 transition-colors",
          canGoPrevious
            ? "cursor-pointer hover:bg-[var(--oh-interactive-hover-low)]"
            : "cursor-not-allowed opacity-50",
        )}
        aria-label="Previous page"
      >
        <ChevronLeft width={16} height={16} active={canGoPrevious} />
      </button>

      {/* Page numbers */}
      {getPageNumbers().map((page, index) =>
        page === "ellipsis" ? (
          <span key={`ellipsis-${index}`} className="px-2 text-sm text-muted">
            ...
          </span>
        ) : (
          <button
            key={page}
            type="button"
            onClick={() => onPageChange(page)}
            className={cn(
              "h-8 min-w-[32px] cursor-pointer rounded-md px-2 text-sm transition-colors",
              currentPage === page
                ? "bg-primary font-medium text-[var(--oh-color-base)]"
                : "text-muted hover:bg-[var(--oh-interactive-hover-low)]",
            )}
            aria-current={currentPage === page ? "page" : undefined}
          >
            {page}
          </button>
        ),
      )}

      {/* Next button */}
      <button
        type="button"
        onClick={() => onPageChange(currentPage + 1)}
        disabled={!canGoNext}
        className={cn(
          "rounded-md p-2 transition-colors",
          canGoNext
            ? "cursor-pointer hover:bg-[var(--oh-interactive-hover-low)]"
            : "cursor-not-allowed opacity-50",
        )}
        aria-label="Next page"
      >
        <ChevronRight width={16} height={16} active={canGoNext} />
      </button>
    </nav>
  );
}
