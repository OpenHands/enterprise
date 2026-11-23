import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useClampPage } from "#/hooks/use-clamp-page";

describe("useClampPage", () => {
  it("steps back to the last page when the current page no longer exists", () => {
    const setPage = vi.fn();

    renderHook(() => useClampPage(3, 2, setPage));

    expect(setPage).toHaveBeenCalledExactlyOnceWith(2);
  });

  it.each([
    { page: 2, totalPages: 2 },
    { page: 1, totalPages: 5 },
    { page: 1, totalPages: 0 },
    { page: 4, totalPages: 0 },
  ])(
    "leaves page $page alone with $totalPages pages",
    ({ page, totalPages }) => {
      const setPage = vi.fn();

      renderHook(() => useClampPage(page, totalPages, setPage));

      expect(setPage).not.toHaveBeenCalled();
    },
  );
});
