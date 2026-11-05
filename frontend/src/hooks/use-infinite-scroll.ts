import { useEffect, useRef, useCallback } from "react";

interface UseInfiniteScrollOptions {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  threshold?: number;
  itemCount?: number;
}

export const useInfiniteScroll = ({
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  threshold = 100,
  itemCount,
}: UseInfiniteScrollOptions) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const lastAutoFillKeyRef = useRef<string | null>(null);

  const handleScroll = useCallback(() => {
    if (!containerRef.current || isFetchingNextPage || !hasNextPage) {
      return;
    }

    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const isNearBottom = scrollTop + clientHeight >= scrollHeight - threshold;

    if (isNearBottom) {
      fetchNextPage();
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, threshold]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    container.addEventListener("scroll", handleScroll);
    return () => {
      container.removeEventListener("scroll", handleScroll);
    };
  }, [handleScroll]);

  // Auto-fill: if the loaded content doesn't overflow the container, no
  // scroll event can ever fire, so request the next page until the content
  // overflows or pages run out. One attempt per (itemCount, scrollHeight)
  // state, so a failing fetch cannot loop.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !hasNextPage || isFetchingNextPage) return;

    const { scrollHeight, clientHeight } = container;
    if (clientHeight === 0) return; // not laid out yet

    if (scrollHeight > clientHeight + threshold) return;

    const autoFillKey = `${itemCount ?? -1}:${scrollHeight}`;
    if (lastAutoFillKeyRef.current === autoFillKey) return;
    lastAutoFillKeyRef.current = autoFillKey;

    fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, threshold, itemCount]);

  return containerRef;
};
