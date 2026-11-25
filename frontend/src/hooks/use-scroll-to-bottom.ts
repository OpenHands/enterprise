import { RefObject, useState, useCallback, useRef } from "react";

export function useScrollToBottom(scrollRef: RefObject<HTMLDivElement | null>) {
  // Track whether the user is currently near the bottom of the scroll area.
  // Used by consumers to decide whether to scroll when new UI elements appear.
  // NOT used for automatic content-following.
  const [autoscroll, setAutoscroll] = useState(true);

  const [hitBottom, setHitBottom] = useState(true);

  // Store previous scroll position to detect scroll direction
  const prevScrollTopRef = useRef<number>(0);

  const isAtBottom = useCallback((element: HTMLElement): boolean => {
    const bottomThreshold = 20;
    const bottomPosition = element.scrollTop + element.clientHeight;
    return bottomPosition >= element.scrollHeight - bottomThreshold;
  }, []);

  const onChatBodyScroll = useCallback(
    (e: HTMLElement) => {
      const isCurrentlyAtBottom = isAtBottom(e);
      setHitBottom(isCurrentlyAtBottom);

      const currentScrollTop = e.scrollTop;

      const isScrollingUp = currentScrollTop < prevScrollTopRef.current;

      prevScrollTopRef.current = currentScrollTop;

      if (isScrollingUp) {
        setAutoscroll(false);
      }

      if (isCurrentlyAtBottom) {
        setAutoscroll(true);
      }
    },
    [isAtBottom],
  );

  // Scroll to bottom on manual click only
  const scrollDomToBottom = useCallback(() => {
    const dom = scrollRef.current;
    if (dom) {
      requestAnimationFrame(() => {
        setAutoscroll(true);
        setHitBottom(true);

        dom.scrollTop = dom.scrollHeight;
      });
    }
  }, [scrollRef]);

  return {
    scrollRef,
    autoScroll: autoscroll,
    setAutoScroll: setAutoscroll,
    scrollDomToBottom,
    hitBottom,
    setHitBottom,
    onChatBodyScroll,
  };
}
