import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { HorizontalScrollFade } from "#/components/shared/horizontal-scroll-fade";

function mockScrollMetrics(
  element: HTMLElement,
  metrics: { scrollWidth: number; clientWidth: number; scrollLeft: number },
) {
  Object.defineProperty(element, "scrollWidth", {
    configurable: true,
    value: metrics.scrollWidth,
  });
  Object.defineProperty(element, "clientWidth", {
    configurable: true,
    value: metrics.clientWidth,
  });
  Object.defineProperty(element, "scrollLeft", {
    configurable: true,
    writable: true,
    value: metrics.scrollLeft,
  });
}

describe("HorizontalScrollFade", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe = vi.fn();

        unobserve = vi.fn();

        disconnect = vi.fn();
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders animated edge fades that toggle with horizontal scroll", () => {
    render(
      <HorizontalScrollFade>
        <table>
          <tbody>
            <tr>
              <td>Wide content</td>
            </tr>
          </tbody>
        </table>
      </HorizontalScrollFade>,
    );

    const scroller = screen.getByTestId("table-scroll");
    const leftFade = screen.getByTestId("table-scroll-fade-left");
    const rightFade = screen.getByTestId("table-scroll-fade-right");

    mockScrollMetrics(scroller, {
      scrollWidth: 900,
      clientWidth: 320,
      scrollLeft: 0,
    });
    fireEvent.scroll(scroller);

    expect(rightFade).toHaveAttribute("data-visible", "true");
    expect(rightFade).toHaveClass("opacity-100");
    expect(leftFade).toHaveAttribute("data-visible", "false");
    expect(leftFade).toHaveClass("opacity-0");

    mockScrollMetrics(scroller, {
      scrollWidth: 900,
      clientWidth: 320,
      scrollLeft: 580,
    });
    fireEvent.scroll(scroller);

    expect(leftFade).toHaveAttribute("data-visible", "true");
    expect(leftFade).toHaveClass("opacity-100");
    expect(rightFade).toHaveAttribute("data-visible", "false");
    expect(rightFade).toHaveClass("opacity-0");
  });
});
