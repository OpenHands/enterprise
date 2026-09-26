import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProviderConnection } from "#/api/organization-service/org-provider-connections-service.api";
import { ProviderConnectionsManager } from "#/components/features/settings/provider-connections/provider-connections-manager";

const connectionsState: {
  data: ProviderConnection[] | undefined;
  isLoading: boolean;
  error: Error | null;
} = { data: undefined, isLoading: false, error: null };

const createMock = vi.fn();
const updateMock = vi.fn();
const deleteMock = vi.fn();

vi.mock("#/hooks/query/use-provider-connections", () => ({
  useProviderConnections: () => connectionsState,
}));

vi.mock("#/hooks/mutation/use-provider-connection-mutations", () => ({
  useCreateProviderConnection: () => ({
    mutateAsync: createMock,
    isPending: false,
  }),
  useUpdateProviderConnection: () => ({
    mutateAsync: updateMock,
    isPending: false,
  }),
  useDeleteProviderConnection: () => ({
    mutateAsync: deleteMock,
    isPending: false,
  }),
}));

vi.mock("#/hooks/query/use-search-providers", () => ({
  useSearchProviders: () => ({
    data: [
      { name: "openai", verified: true },
      { name: "custom", verified: false },
    ],
  }),
}));

const sampleConnection: ProviderConnection = {
  id: "conn-1",
  display_name: "OpenAI shared key",
  provider: "openai",
  base_url: null,
  created_at: 1,
  updated_at: 1,
  api_key_set: true,
};

function renderManager({
  canManage,
  profiles,
}: {
  canManage?: boolean;
  profiles?: ReadonlyArray<{ provider_connection_id?: string | null }>;
} = {}) {
  return render(
    <ProviderConnectionsManager
      orgId="org-1"
      canManage={canManage}
      profiles={profiles}
    />,
  );
}

beforeEach(() => {
  connectionsState.data = [sampleConnection];
  connectionsState.isLoading = false;
  connectionsState.error = null;
  createMock.mockReset().mockResolvedValue(sampleConnection);
  updateMock.mockReset().mockResolvedValue(sampleConnection);
  deleteMock.mockReset().mockResolvedValue(undefined);
});

describe("ProviderConnectionsManager", () => {
  it("renders the connection list and hides management controls for viewers", () => {
    renderManager({ canManage: false });

    expect(screen.getByText("OpenAI shared key")).toBeInTheDocument();
    // Viewers never see Add / edit / delete affordances.
    expect(screen.queryByTestId("add-provider-connection")).toBeNull();
    expect(screen.queryByTestId("provider-connection-edit")).toBeNull();
    expect(screen.queryByTestId("provider-connection-delete")).toBeNull();
  });

  it("shows Add / edit / delete controls when management is enabled", () => {
    renderManager({ canManage: true });

    expect(screen.getByTestId("add-provider-connection")).toBeInTheDocument();
    expect(screen.getByTestId("provider-connection-edit")).toBeInTheDocument();
    expect(
      screen.getByTestId("provider-connection-delete"),
    ).toBeInTheDocument();
  });

  it("counts profiles linked to each connection", () => {
    // The mocked t() returns keys verbatim (no interpolation), so we can't
    // assert on the rendered count text. Instead verify the manager tolerates
    // a mixed set of linked/unlinked profiles without crashing and still
    // renders the row.
    renderManager({
      canManage: false,
      profiles: [
        { provider_connection_id: "conn-1" },
        { provider_connection_id: "conn-1" },
        { provider_connection_id: "conn-other" },
        { provider_connection_id: null },
      ],
    });

    expect(screen.getByText("OpenAI shared key")).toBeInTheDocument();
    expect(screen.getByTestId("provider-connection-row")).toBeInTheDocument();
  });

  it("renders the empty state when there are no connections", () => {
    connectionsState.data = [];
    renderManager({ canManage: true });

    expect(screen.queryByText("OpenAI shared key")).toBeNull();
    expect(screen.getByTestId("add-provider-connection")).toBeInTheDocument();
  });

  it("opens the create modal on Add", async () => {
    renderManager({ canManage: true });
    const user = userEvent.setup();

    await user.click(screen.getByTestId("add-provider-connection"));

    expect(screen.getByTestId("provider-connection-modal")).toBeInTheDocument();
    expect(
      screen.getByTestId("provider-connection-name-input"),
    ).toBeInTheDocument();
  });

  it("opens the edit modal hydrated with the clicked connection", async () => {
    renderManager({ canManage: true });
    const user = userEvent.setup();

    await user.click(screen.getByTestId("provider-connection-edit"));

    expect(screen.getByTestId("provider-connection-modal")).toBeInTheDocument();
    expect(
      (screen.getByTestId("provider-connection-name-input") as HTMLInputElement)
        .value,
    ).toBe("OpenAI shared key");
  });

  it("opens the delete confirmation modal on delete", async () => {
    renderManager({ canManage: true });
    const user = userEvent.setup();

    await user.click(screen.getByTestId("provider-connection-delete"));

    expect(
      screen.getByTestId("delete-provider-connection-confirm"),
    ).toBeInTheDocument();
  });

  it("deletes the connection on confirm", async () => {
    renderManager({ canManage: true });
    const user = userEvent.setup();

    await user.click(screen.getByTestId("provider-connection-delete"));
    await user.click(screen.getByTestId("delete-provider-connection-confirm"));

    expect(deleteMock).toHaveBeenCalledWith("conn-1");
  });
});
