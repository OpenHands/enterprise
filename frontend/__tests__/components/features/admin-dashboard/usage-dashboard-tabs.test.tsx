import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ModelsTab,
  OverviewTab,
} from "#/components/features/admin-dashboard/usage-dashboard-tabs";
import * as utilsModule from "#/utils/utils";

// jsdom's Blob polyfill doesn't implement text()/arrayBuffer(); FileReader
// is the reliable way to read Blob contents in this test environment.
const readBlobAsText = (blob: Blob) =>
  new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });

describe("Usage & Monitoring Export CSV buttons", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("downloads a CSV of the chart data when clicking Export CSV on the Overview tab", async () => {
    const downloadBlobSpy = vi
      .spyOn(utilsModule, "downloadBlob")
      .mockImplementation(() => {});
    const user = userEvent.setup();

    render(
      <OverviewTab
        usageConversations={10}
        activeConversations={2}
        avgCostPerConversation={1.23}
        totalSpend="$12.30"
        timeWindowLabel="30D"
        chartData={[
          { date: "2026-07-01", value: 3 },
          { date: "2026-07-02", value: 5 },
        ]}
        agentSpendRows={[]}
        agentSpendTotal={0}
      />,
    );

    await user.click(screen.getByRole("button", { name: /export csv/i }));

    expect(downloadBlobSpy).toHaveBeenCalledTimes(1);
    const [blob, filename] = downloadBlobSpy.mock.calls[0];
    expect(filename).toMatch(/^conversations_per_day_\d{8}_\d{6}\.csv$/);
    const text = await readBlobAsText(blob);
    expect(text).toBe("date,conversations_started\n2026-07-01,3\n2026-07-02,5");
  });

  it("disables the Overview Export CSV button when there is no chart data", () => {
    render(
      <OverviewTab
        usageConversations={0}
        activeConversations={0}
        avgCostPerConversation={0}
        totalSpend="$0.00"
        timeWindowLabel="30D"
        chartData={[]}
        agentSpendRows={[]}
        agentSpendTotal={0}
      />,
    );

    expect(screen.getByRole("button", { name: /export csv/i })).toBeDisabled();
  });

  it("downloads a CSV of model usage rows when clicking Export CSV on the Models tab", async () => {
    const downloadBlobSpy = vi
      .spyOn(utilsModule, "downloadBlob")
      .mockImplementation(() => {});
    const user = userEvent.setup();

    render(
      <ModelsTab
        modelSearch=""
        onModelSearchChange={vi.fn()}
        filteredModels={[
          {
            model_name: "gpt-4",
            conversation_count: 4,
            total_tokens: 1000,
            avgTokens: 250,
            avgCost: 0.5,
            total_cost: 2,
          },
        ]}
      />,
    );

    await user.click(screen.getByRole("button", { name: /export csv/i }));

    expect(downloadBlobSpy).toHaveBeenCalledTimes(1);
    const [blob, filename] = downloadBlobSpy.mock.calls[0];
    expect(filename).toMatch(/^model_usage_\d{8}_\d{6}\.csv$/);
    const text = await readBlobAsText(blob);
    expect(text).toBe(
      "model_name,conversation_count,total_tokens,avg_tokens_per_conversation,avg_cost_per_conversation,total_cost\ngpt-4,4,1000,250,0.50,2.00",
    );
  });

  it("disables the Models Export CSV button when there are no rows", () => {
    render(
      <ModelsTab
        modelSearch=""
        onModelSearchChange={vi.fn()}
        filteredModels={[]}
      />,
    );

    expect(screen.getByRole("button", { name: /export csv/i })).toBeDisabled();
  });
});
