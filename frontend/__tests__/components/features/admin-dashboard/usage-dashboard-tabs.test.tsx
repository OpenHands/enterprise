import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "test-utils";
import OptionService from "#/api/option-service/option-service.api";
import SettingsService from "#/api/settings-service/settings-service.api";
import {
  AssociatedPrCell,
  ModelsTab,
  OverviewTab,
} from "#/components/features/admin-dashboard/usage-dashboard-tabs";
import { useSettings } from "#/hooks/query/use-settings";
import { MOCK_DEFAULT_USER_SETTINGS } from "#/mocks/handlers";
import { createMockWebClientConfig } from "#/mocks/settings-handlers";
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

  it("keeps compare series in the existing chart card", async () => {
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
        chartSubtitle="30D · 2 organizations"
        chartData={[
          { date: "2026-07-01", value: 8 },
          { date: "2026-07-02", value: 11 },
        ]}
        compareSeries={[
          {
            id: "acme",
            label: "Acme Corp",
            color: "#7c3aed",
            values: [3, 5],
          },
          {
            id: "northwind",
            label: "Northwind",
            color: "#2563eb",
            values: [5, 6],
          },
        ]}
        agentSpendRows={[]}
        agentSpendTotal={0}
      />,
    );

    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(2);
    expect(
      screen.getByRole("heading", { name: "Conversations started per day" }),
    ).toBeInTheDocument();
    expect(screen.getByText("30D · 2 organizations")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("Northwind")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /export csv/i }));
    const [blob] = downloadBlobSpy.mock.calls[0];
    const text = await readBlobAsText(blob);
    expect(text).toBe(
      "date,Acme Corp,Northwind\n2026-07-01,3,5\n2026-07-02,5,6",
    );
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

describe("AssociatedPrCell", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("links each PR to its provider page in a new tab, labeled org/repo #N", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        provider_default_hosts: { github: "github.com" },
      }),
    );

    renderWithProviders(
      <AssociatedPrCell
        conversation={{
          pr_number: [295, 301],
          selected_repository: "acme/widgets",
          git_provider: "github",
        }}
      />,
    );

    const first = await screen.findByRole("link", {
      name: "acme/widgets #295",
    });
    expect(first).toHaveAttribute(
      "href",
      "https://github.com/acme/widgets/pull/295",
    );
    expect(first).toHaveAttribute("target", "_blank");
    expect(first).toHaveAttribute("rel", "noopener noreferrer");
    expect(
      screen.getByRole("link", { name: "acme/widgets #301" }),
    ).toHaveAttribute("href", "https://github.com/acme/widgets/pull/301");
  });

  // The admin views other members' PRs, so the viewer's own token host is not
  // the host of the PR.
  it("ignores the viewer's own provider host", async () => {
    vi.spyOn(OptionService, "getConfig").mockResolvedValue(
      createMockWebClientConfig({
        provider_default_hosts: { gitlab: "gitlab.com" },
      }),
    );
    vi.spyOn(SettingsService, "getSettings").mockResolvedValue({
      ...MOCK_DEFAULT_USER_SETTINGS,
      provider_tokens_set: { gitlab: "gitlab.viewer.dev" },
    });
    // Shares the settings query, so the assertion runs after the viewer's
    // settings load.
    function SettingsLoaded() {
      const { data } = useSettings();
      return data ? <span data-testid="settings-loaded" /> : null;
    }

    renderWithProviders(
      <>
        <SettingsLoaded />
        <AssociatedPrCell
          conversation={{
            pr_number: [7],
            selected_repository: "platform/api",
            git_provider: "gitlab",
          }}
        />
      </>,
    );
    await screen.findByTestId("settings-loaded");

    expect(
      screen.getByRole("link", { name: "platform/api #7" }),
    ).toHaveAttribute(
      "href",
      "https://gitlab.com/platform/api/-/merge_requests/7",
    );
  });

  it("shows the PR number as plain text when the repository is unknown", () => {
    renderWithProviders(
      <AssociatedPrCell
        conversation={{
          pr_number: [295],
          selected_repository: null,
          git_provider: null,
        }}
      />,
    );

    expect(screen.getByText("#295")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows a dash when there are no PRs", () => {
    renderWithProviders(<AssociatedPrCell conversation={{ pr_number: [] }} />);

    expect(screen.getByText("-")).toBeInTheDocument();
  });
});
