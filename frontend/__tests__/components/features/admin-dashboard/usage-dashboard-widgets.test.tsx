import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  AreaChart,
  MultiLineChart,
} from "#/components/features/admin-dashboard/usage-dashboard-widgets";

describe("usage dashboard chart tooltips", () => {
  it("shows an area-chart value tooltip on hover", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <div style={{ width: 200, height: 120 }}>
        <AreaChart
          data={[
            { date: "2026-07-14", value: 4 },
            { date: "2026-07-15", value: 9 },
            { date: "2026-07-16", value: 6 },
          ]}
        />
      </div>,
    );

    const chart = container.querySelector(".relative") as HTMLElement;
    await user.pointer({
      target: chart,
      coords: { clientX: 100, clientY: 40 },
    });

    expect(screen.getByTestId("usage-chart-tooltip")).toHaveTextContent(
      "conversations",
    );
    expect(screen.getByTestId("usage-chart-hover-dot")).toBeInTheDocument();
  });

  it("shows multi-series values in the tooltip on hover", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <div style={{ width: 200, height: 120 }}>
        <MultiLineChart
          dates={["2026-07-14", "2026-07-15", "2026-07-16"]}
          series={[
            {
              id: "a",
              label: "Acme",
              color: "#fff",
              values: [1, 4, 2],
            },
            {
              id: "b",
              label: "Beta",
              color: "#0ff",
              values: [2, 3, 5],
            },
          ]}
        />
      </div>,
    );

    const chart = container.querySelector(".relative") as HTMLElement;
    await user.pointer({
      target: chart,
      coords: { clientX: 100, clientY: 40 },
    });

    const tooltip = screen.getByTestId("usage-chart-tooltip");
    expect(tooltip).toHaveTextContent("Acme");
    expect(tooltip).toHaveTextContent("Beta");
  });
});
