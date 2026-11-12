import { useEffect } from "react";

/** Steps back to the last page when rows are removed from under the current one. */
export function useClampPage(
  page: number,
  totalPages: number,
  setPage: (page: number) => void,
) {
  useEffect(() => {
    if (totalPages > 0 && page > totalPages) {
      setPage(totalPages);
    }
  }, [page, totalPages, setPage]);
}
