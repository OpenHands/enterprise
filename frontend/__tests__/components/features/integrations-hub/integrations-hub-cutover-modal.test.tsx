import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IntegrationsHubCutoverModal } from "#/components/features/integrations-hub/integrations-hub-cutover-modal";
import type { LegacyCutoverItem } from "#/components/features/integrations-hub/legacy-integrations-cutover";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { name?: string }) =>
      options?.name ? `${key}:${options.name}` : key,
  }),
}));

vi.mock(
  "#/components/features/settings/git-settings/integration-provider-icon",
  () => ({
    IntegrationProviderIcon: ({ provider }: { provider: string }) => (
      <span data-testid={`icon-${provider}`} />
    ),
  }),
);

describe("IntegrationsHubCutoverModal", () => {
  const items: LegacyCutoverItem[] = [
    {
      id: "github",
      hubSlug: "github",
      canReconnectInHub: true,
    },
    {
      id: "gitlab",
      hubSlug: "gitlab",
      canReconnectInHub: false,
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists reconnectable and unavailable legacy integrations", () => {
    render(
      <IntegrationsHubCutoverModal
        items={items}
        onClose={vi.fn()}
        onReconnect={vi.fn()}
      />,
    );

    expect(
      screen.getByTestId("integrations-hub-cutover-modal"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-cutover-item-github"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("integrations-hub-cutover-reconnect-github"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("integrations-hub-cutover-reconnect-gitlab"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("INTEGRATIONS_HUB$CUTOVER_ITEM_HINT_UNAVAILABLE:GitLab"),
    ).toBeInTheDocument();
  });

  it("calls onReconnect and onClose from actions", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onReconnect = vi.fn();

    render(
      <IntegrationsHubCutoverModal
        items={items}
        onClose={onClose}
        onReconnect={onReconnect}
      />,
    );

    await user.click(
      screen.getByTestId("integrations-hub-cutover-reconnect-github"),
    );
    expect(onReconnect).toHaveBeenCalledWith("github");

    await user.click(screen.getByTestId("integrations-hub-cutover-got-it"));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the empty-body copy when there are no legacy items", () => {
    render(
      <IntegrationsHubCutoverModal
        items={[]}
        onClose={vi.fn()}
        onReconnect={vi.fn()}
      />,
    );

    expect(
      screen.getByText("INTEGRATIONS_HUB$CUTOVER_BODY_NONE"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("integrations-hub-cutover-list"),
    ).not.toBeInTheDocument();
  });
});
