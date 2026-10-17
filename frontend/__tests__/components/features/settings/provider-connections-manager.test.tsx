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

// The global `t` mock returns keys verbatim; expose `count` here so the
// per-connection profile count can be asserted.
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-i18next")>()),
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      typeof options?.count === "number" ? `${key}:${options.count}` : key,
    i18n: { language: "en", exists: () => false },
  }),
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
  profiles,
}: {
  profiles?: ReadonlyArray<{ provider_connection_id?: string | null }>;
} = {}) {
  return render(
    <ProviderConnectionsManager orgId="org-1" profiles={profiles} />,
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
  it("renders the connection list with Add / edit / delete controls", () => {
    renderManager();

    expect(screen.getByText("OpenAI shared key")).toBeInTheDocument();
    expect(screen.getByTestId("add-provider-connection")).toBeInTheDocument();
    expect(screen.getByTestId("provider-connection-edit")).toBeInTheDocument();
    expect(
      screen.getByTestId("provider-connection-delete"),
    ).toBeInTheDocument();
  });

  it("counts profiles linked to each connection", () => {
    renderManager({
      profiles: [
        { provider_connection_id: "conn-1" },
        { provider_connection_id: "conn-1" },
        { provider_connection_id: "conn-other" },
        { provider_connection_id: null },
      ],
    });

    expect(screen.getByTestId("provider-connection-row")).toHaveTextContent(
      "SETTINGS$PROVIDER_CONNECTION_MODEL_COUNT:2",
    );
  });

  it("shows a zero count when no profile is linked", () => {
    renderManager();

    expect(screen.getByTestId("provider-connection-row")).toHaveTextContent(
      "SETTINGS$PROVIDER_CONNECTION_MODEL_COUNT:0",
    );
  });

  it("renders the empty state when there are no connections", () => {
    connectionsState.data = [];
    renderManager();

    expect(screen.queryByText("OpenAI shared key")).toBeNull();
    expect(screen.getByTestId("add-provider-connection")).toBeInTheDocument();
  });

  it("opens the create modal on Add", async () => {
    renderManager();
    const user = userEvent.setup();

    await user.click(screen.getByTestId("add-provider-connection"));

    expect(screen.getByTestId("provider-connection-modal")).toBeInTheDocument();
    expect(
      screen.getByTestId("provider-connection-name-input"),
    ).toBeInTheDocument();
  });

  it("opens the edit modal hydrated with the clicked connection", async () => {
    renderManager();
    const user = userEvent.setup();

    await user.click(screen.getByTestId("provider-connection-edit"));

    expect(screen.getByTestId("provider-connection-modal")).toBeInTheDocument();
    expect(
      (screen.getByTestId("provider-connection-name-input") as HTMLInputElement)
        .value,
    ).toBe("OpenAI shared key");
  });

  it("opens the delete confirmation modal on delete", async () => {
    renderManager();
    const user = userEvent.setup();

    await user.click(screen.getByTestId("provider-connection-delete"));

    expect(
      screen.getByTestId("delete-provider-connection-confirm"),
    ).toBeInTheDocument();
  });

  it("blocks deleting a connection that profiles still use", async () => {
    renderManager({ profiles: [{ provider_connection_id: "conn-1" }] });
    const user = userEvent.setup();

    await user.click(screen.getByTestId("provider-connection-delete"));

    const confirm = screen.getByTestId("delete-provider-connection-confirm");
    expect(confirm).toBeDisabled();
    expect(
      screen.getByText("SETTINGS$PROVIDER_CONNECTION_DELETE_BLOCKED:1"),
    ).toBeInTheDocument();
    await user.click(confirm);
    expect(deleteMock).not.toHaveBeenCalled();
  });

  it("deletes the connection on confirm", async () => {
    renderManager();
    const user = userEvent.setup();

    await user.click(screen.getByTestId("provider-connection-delete"));
    await user.click(screen.getByTestId("delete-provider-connection-confirm"));

    expect(deleteMock).toHaveBeenCalledWith("conn-1");
  });
});
