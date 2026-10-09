import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LlmProfileSummary } from "#/api/settings-service/profiles-service.api";
import { ProfilesBody } from "#/components/features/settings/profiles-body";

const profiles: LlmProfileSummary[] = [
  { name: "p1", model: "openai/gpt-4o", base_url: null, api_key_set: true },
  { name: "p2", model: "anthropic/claude", base_url: null, api_key_set: true },
];

const linkedProfiles: LlmProfileSummary[] = [
  {
    name: "p1",
    model: "openai/gpt-4o",
    base_url: null,
    api_key_set: true,
    provider_connection_id: "conn-1",
  },
  {
    name: "p2",
    model: "openai/o3",
    base_url: null,
    api_key_set: true,
    provider_connection_id: "conn-1",
  },
  {
    name: "p3",
    model: "anthropic/claude",
    base_url: null,
    api_key_set: true,
    provider_connection_id: "conn-2",
  },
  { name: "p4", model: "openhands/sonnet", base_url: null, api_key_set: true },
];

describe("ProfilesBody", () => {
  it("shows a loading spinner while isLoading is true", () => {
    render(
      <ProfilesBody
        isLoading
        loadError={null}
        profiles={[]}
        active={null}
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        isActivating={false}
      />,
    );

    expect(screen.getByTestId("loading-spinner")).toBeInTheDocument();
  });

  it("shows the load-error paragraph when loadError is set", () => {
    render(
      <ProfilesBody
        isLoading={false}
        loadError={new Error("boom")}
        profiles={[]}
        active={null}
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        isActivating={false}
      />,
    );

    expect(
      screen.getByText("SETTINGS$PROFILES_LOAD_ERROR"),
    ).toBeInTheDocument();
  });

  it("shows the empty-state paragraph when no profiles are passed", () => {
    render(
      <ProfilesBody
        isLoading={false}
        loadError={null}
        profiles={[]}
        active={null}
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        isActivating={false}
      />,
    );

    expect(screen.getByText("SETTINGS$PROFILES_EMPTY")).toBeInTheDocument();
  });

  it("renders one ProfileRow per profile and marks the active one", () => {
    render(
      <ProfilesBody
        isLoading={false}
        loadError={null}
        profiles={profiles}
        active="p1"
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        isActivating={false}
      />,
    );

    const rows = screen.getAllByTestId("profile-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("p1");
    expect(rows[0]).toHaveTextContent("SETTINGS$PROFILE_ACTIVE_BADGE");
    expect(rows[1]).not.toHaveTextContent("SETTINGS$PROFILE_ACTIVE_BADGE");
  });

  it("renders profiles without action menus when management is disabled", () => {
    render(
      <ProfilesBody
        isLoading={false}
        loadError={null}
        profiles={profiles}
        active="p1"
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        isActivating={false}
        canManage={false}
      />,
    );

    expect(screen.getAllByTestId("profile-row")).toHaveLength(2);
    expect(
      screen.queryByTestId("profile-menu-trigger"),
    ).not.toBeInTheDocument();
  });

  it("renders a flat list when no profile links to a connection", () => {
    render(
      <ProfilesBody
        isLoading={false}
        loadError={null}
        profiles={profiles}
        active="p1"
        connectionNamesById={{ "conn-1": "Anthropic key" }}
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        isActivating={false}
      />,
    );

    expect(screen.getAllByTestId("profile-row")).toHaveLength(2);
    expect(
      screen.queryByTestId("profile-group-header"),
    ).not.toBeInTheDocument();
  });

  it("groups linked profiles under their connection's display name", () => {
    render(
      <ProfilesBody
        isLoading={false}
        loadError={null}
        profiles={linkedProfiles}
        active="p1"
        connectionNamesById={{
          "conn-1": "OpenAI key",
          "conn-2": "Anthropic key",
        }}
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        isActivating={false}
      />,
    );

    const headers = screen.getAllByTestId("profile-group-header");
    expect(headers).toHaveLength(3);
    expect(headers[0]).toHaveTextContent("OpenAI key");
    expect(headers[1]).toHaveTextContent("Anthropic key");
    expect(headers[2]).toHaveTextContent("SETTINGS$PROFILES_UNGROUPED");

    const rows = screen.getAllByTestId("profile-row");
    expect(rows).toHaveLength(4);
    // Group order: conn-1 (p1, p2), conn-2 (p3), unlinked (p4).
    expect(rows[0]).toHaveTextContent("p1");
    expect(rows[1]).toHaveTextContent("p2");
    expect(rows[2]).toHaveTextContent("p3");
    expect(rows[3]).toHaveTextContent("p4");
  });

  it("falls back to the connection id when no display name is known", () => {
    render(
      <ProfilesBody
        isLoading={false}
        loadError={null}
        profiles={[linkedProfiles[0]]}
        active="p1"
        connectionNamesById={{}}
        onActivate={vi.fn()}
        onEdit={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
        isActivating={false}
      />,
    );

    expect(screen.getByTestId("profile-group-header")).toHaveTextContent(
      "conn-1",
    );
  });
});
