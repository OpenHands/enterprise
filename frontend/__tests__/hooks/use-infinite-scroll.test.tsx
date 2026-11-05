import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useInfiniteScroll } from "#/hooks/use-infinite-scroll";

interface ConsumerProps {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  itemCount?: number;
}

function Consumer(props: ConsumerProps) {
  const containerRef = useInfiniteScroll(props);
  return <div ref={containerRef} />;
}

const mockContainerSize = (scrollHeight: number, clientHeight: number) => {
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(
    scrollHeight,
  );
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(
    clientHeight,
  );
};

describe("useInfiniteScroll auto-fill", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("fetches the next page when the content does not overflow the container", () => {
    mockContainerSize(300, 500);
    const fetchNextPage = vi.fn();

    render(
      <Consumer
        hasNextPage
        isFetchingNextPage={false}
        fetchNextPage={fetchNextPage}
        itemCount={1}
      />,
    );

    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });

  it("does not fetch when the content already overflows the container", () => {
    mockContainerSize(2000, 500);
    const fetchNextPage = vi.fn();

    render(
      <Consumer
        hasNextPage
        isFetchingNextPage={false}
        fetchNextPage={fetchNextPage}
        itemCount={1}
      />,
    );

    expect(fetchNextPage).not.toHaveBeenCalled();
  });

  it("does not re-attempt the same state, so a failing fetch cannot loop", () => {
    mockContainerSize(300, 500);
    const firstAttempt = vi.fn();

    const { rerender } = render(
      <Consumer
        hasNextPage
        isFetchingNextPage={false}
        fetchNextPage={firstAttempt}
        itemCount={1}
      />,
    );
    expect(firstAttempt).toHaveBeenCalledTimes(1);

    // A failed fetch settles without changing item count or content height.
    const secondAttempt = vi.fn();
    rerender(
      <Consumer
        hasNextPage
        isFetchingNextPage={false}
        fetchNextPage={secondAttempt}
        itemCount={1}
      />,
    );

    expect(secondAttempt).not.toHaveBeenCalled();
  });

  it("attempts again when new items arrive without overflowing the container", () => {
    mockContainerSize(300, 500);
    const fetchNextPage = vi.fn();

    const { rerender } = render(
      <Consumer
        hasNextPage
        isFetchingNextPage={false}
        fetchNextPage={fetchNextPage}
        itemCount={1}
      />,
    );
    rerender(
      <Consumer
        hasNextPage
        isFetchingNextPage={false}
        fetchNextPage={fetchNextPage}
        itemCount={2}
      />,
    );

    expect(fetchNextPage).toHaveBeenCalledTimes(2);
  });

  it("does nothing before the container has been laid out", () => {
    mockContainerSize(0, 0);
    const fetchNextPage = vi.fn();

    render(
      <Consumer
        hasNextPage
        isFetchingNextPage={false}
        fetchNextPage={fetchNextPage}
        itemCount={1}
      />,
    );

    expect(fetchNextPage).not.toHaveBeenCalled();
  });
});
