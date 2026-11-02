import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OrgMcpServerForm } from "#/components/features/settings/mcp-settings/org-mcp-server-form";
import {
  OrgMcpServerView,
  REDACTED_MCP_SECRET,
  secretReference,
} from "#/utils/org-mcp-servers";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const BIFROST_URL = "https://bifrost.example/mcp";
const DATADOG_URL = "https://dd.example/mcp";
const OAUTH_URL = "https://issues.example/mcp";
const noop = () => {};

function fill(testId: string, value: string) {
  fireEvent.change(screen.getByTestId(testId), { target: { value } });
}

function storedOrgListing(): OrgMcpServerView {
  return {
    name: "datadog-shared",
    managedBy: "org",
    type: "shttp",
    url: DATADOG_URL,
    auth: "bearer",
    credential: REDACTED_MCP_SECRET,
    secretNames: [],
    server: {},
  };
}

function oauthListing(
  overrides: Partial<OrgMcpServerView> = {},
): OrgMcpServerView {
  return {
    name: "issue-tracker",
    managedBy: "org",
    type: "shttp",
    url: OAUTH_URL,
    auth: "oauth",
    secretNames: [],
    oauth: {
      clientId: "client-1",
      clientSecret: REDACTED_MCP_SECRET,
      scopes: "read write",
      authorized: true,
    },
    server: {},
    ...overrides,
  };
}

describe("OrgMcpServerForm", () => {
  it("adds a user-managed bearer listing that references the personal secret", () => {
    const onSubmit = vi.fn();
    render(<OrgMcpServerForm mode="add" onSubmit={onSubmit} onCancel={noop} />);

    fill("org-name-input", "bifrost");
    fill("org-url-input", BIFROST_URL);
    fill("org-secret-name-input", "BIFROST_TOKEN");
    fireEvent.click(screen.getByTestId("org-submit-button"));

    expect(onSubmit).toHaveBeenCalledWith({
      name: "bifrost",
      managed_by: "user",
      server: {
        url: BIFROST_URL,
        transport: "http",
        auth: { strategy: "bearer", value: secretReference("BIFROST_TOKEN") },
      },
    });
    // The admin never types a token for a user-managed listing.
    expect(screen.queryByTestId("org-token-input")).not.toBeInTheDocument();
  });

  it("rejects a missing or invalid personal secret name", () => {
    const onSubmit = vi.fn();
    render(<OrgMcpServerForm mode="add" onSubmit={onSubmit} onCancel={noop} />);
    fill("org-name-input", "bifrost");
    fill("org-url-input", BIFROST_URL);

    // Whitespace passes the input's own `required` check; the form trims it.
    fill("org-secret-name-input", "   ");
    fireEvent.click(screen.getByTestId("org-submit-button"));
    expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
      "SETTINGS$MCP_ORG_ERROR_SECRET_NAME_REQUIRED",
    );

    fill("org-secret-name-input", "1-bad-name");
    fireEvent.click(screen.getByTestId("org-submit-button"));
    expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
      "SETTINGS$MCP_ORG_ERROR_SECRET_NAME_INVALID",
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rejects a name the organization already uses or that is reserved", () => {
    const onSubmit = vi.fn();
    render(
      <OrgMcpServerForm
        mode="add"
        existingNames={["Bifrost"]}
        onSubmit={onSubmit}
        onCancel={noop}
      />,
    );
    fill("org-url-input", BIFROST_URL);
    fill("org-secret-name-input", "BIFROST_TOKEN");

    fill("org-name-input", "bifrost");
    fireEvent.click(screen.getByTestId("org-submit-button"));
    expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
      "SETTINGS$MCP_ORG_ERROR_NAME_DUPLICATE",
    );

    fill("org-name-input", "default");
    fireEvent.click(screen.getByTestId("org-submit-button"));
    expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
      "SETTINGS$MCP_ERROR_NAME_INVALID",
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("keeps a stored organization token while editing unless the URL changes", () => {
    const onSubmit = vi.fn();
    render(
      <OrgMcpServerForm
        mode="edit"
        listing={storedOrgListing()}
        onSubmit={onSubmit}
        onCancel={noop}
      />,
    );
    expect(screen.getByTestId("org-name-input")).toHaveAttribute("readonly");

    // Leaving the token blank keeps the stored one (the marker goes back).
    fireEvent.click(screen.getByTestId("org-submit-button"));
    expect(onSubmit).toHaveBeenLastCalledWith({
      name: "datadog-shared",
      managed_by: "org",
      server: {
        url: DATADOG_URL,
        transport: "http",
        auth: { strategy: "bearer", value: REDACTED_MCP_SECRET },
      },
    });

    // A new URL needs the token again: the backend cannot carry it over.
    fill("org-url-input", "https://dd.example/v2/mcp");
    fireEvent.click(screen.getByTestId("org-submit-button"));
    expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
      "SETTINGS$MCP_ORG_ERROR_TOKEN_REQUIRED_AFTER_URL_CHANGE",
    );
    expect(onSubmit).toHaveBeenCalledTimes(1);

    fill("org-token-input", "rotated-key");
    fireEvent.click(screen.getByTestId("org-submit-button"));
    expect(onSubmit).toHaveBeenLastCalledWith({
      name: "datadog-shared",
      managed_by: "org",
      server: {
        url: "https://dd.example/v2/mcp",
        transport: "http",
        auth: { strategy: "bearer", value: "rotated-key" },
      },
    });
  });

  it("shows the secret a user-managed listing asks members for when editing", () => {
    render(
      <OrgMcpServerForm
        mode="edit"
        listing={{
          name: "bifrost",
          managedBy: "user",
          type: "sse",
          url: BIFROST_URL,
          auth: "bearer",
          credential: REDACTED_MCP_SECRET,
          secretNames: ["BIFROST_TOKEN"],
          server: {},
        }}
        onSubmit={noop}
        onCancel={noop}
      />,
    );

    expect(screen.getByTestId("org-secret-name-input")).toHaveValue(
      "BIFROST_TOKEN",
    );
    expect(screen.queryByTestId("org-timeout-input")).not.toBeInTheDocument();
  });

  it("renders helper texts at the small hint size", () => {
    const { unmount } = render(
      <OrgMcpServerForm mode="add" onSubmit={noop} onCancel={noop} />,
    );
    expect(screen.getByText("SETTINGS$MCP_ORG_SECRET_NAME_HELP")).toHaveClass(
      "text-xs",
    );
    unmount();

    render(
      <OrgMcpServerForm
        mode="edit"
        listing={storedOrgListing()}
        onSubmit={noop}
        onCancel={noop}
      />,
    );
    expect(screen.getByText("SETTINGS$MCP_ORG_TOKEN_HELP")).toHaveClass(
      "text-xs",
    );
  });

  it("uses placeholders that name no specific product", () => {
    render(<OrgMcpServerForm mode="add" onSubmit={noop} onCancel={noop} />);

    expect(screen.getByTestId("org-name-input")).toHaveAttribute(
      "placeholder",
      "my-mcp-server",
    );
    expect(screen.getByTestId("org-secret-name-input")).toHaveAttribute(
      "placeholder",
      "MY_MCP_TOKEN",
    );
  });

  describe("OAuth", () => {
    it("offers OAuth when adding a listing and lets each member authorize it", async () => {
      const onSubmit = vi.fn();
      const onTest = vi.fn();
      render(
        <OrgMcpServerForm
          mode="add"
          onSubmit={onSubmit}
          onCancel={noop}
          onTest={onTest}
        />,
      );
      fill("org-name-input", "issue-tracker");
      fill("org-url-input", OAUTH_URL);

      await userEvent.click(screen.getByTestId("org-auth-dropdown"));
      await userEvent.click(
        await screen.findByRole("option", {
          name: "SETTINGS$MCP_ORG_AUTH_OAUTH",
        }),
      );

      // User-managed: nothing to authorize here, and no secret name to ask for.
      expect(screen.getByText("SETTINGS$MCP_ORG_OAUTH_USER_HELP")).toHaveClass(
        "text-xs",
      );
      expect(
        screen.queryByTestId("org-oauth-authorize-button"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("org-secret-name-input"),
      ).not.toBeInTheDocument();
      // The backend cannot test an OAuth server without a consent flow.
      expect(
        screen.queryByTestId("org-mcp-test-connection"),
      ).not.toBeInTheDocument();

      fill("org-oauth-client-id-input", "client-1");
      fill("org-oauth-client-secret-input", "client-secret");
      fill("org-oauth-scopes-input", "read  write");
      fireEvent.click(screen.getByTestId("org-submit-button"));

      expect(onSubmit).toHaveBeenCalledWith({
        name: "issue-tracker",
        managed_by: "user",
        server: {
          url: OAUTH_URL,
          transport: "http",
          auth: {
            strategy: "oauth2",
            authentication: {
              type: "oauth",
              client_id: "client-1",
              client_secret: "client-secret",
              scopes: ["read", "write"],
            },
          },
        },
      });
    });

    it("needs the admin's authorization before saving an organization-managed listing", async () => {
      const onSubmit = vi.fn();
      const onAuthorize = vi
        .fn()
        .mockResolvedValue({ ok: true, jobId: "job-1" });
      render(
        <OrgMcpServerForm
          mode="edit"
          listing={oauthListing({
            oauth: { authorized: false },
          })}
          onSubmit={onSubmit}
          onCancel={noop}
          onAuthorize={onAuthorize}
        />,
      );

      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
        "SETTINGS$MCP_ORG_ERROR_OAUTH_AUTHORIZATION_REQUIRED",
      );
      expect(onSubmit).not.toHaveBeenCalled();

      fireEvent.click(screen.getByTestId("org-oauth-authorize-button"));
      await screen.findByTestId("org-oauth-authorized");
      fireEvent.click(screen.getByTestId("org-submit-button"));

      // The tokens stay on the job: only its id travels.
      expect(onSubmit).toHaveBeenCalledWith({
        name: "issue-tracker",
        managed_by: "org",
        server: {
          url: OAUTH_URL,
          transport: "http",
          auth: { strategy: "oauth2", authentication: { type: "oauth" } },
        },
        oauth_job_id: "job-1",
      });
    });

    it("keeps the authorization form open when the consent flow fails", async () => {
      const onAuthorize = vi
        .fn()
        .mockResolvedValue({ ok: false, reason: "not_completed" });
      render(
        <OrgMcpServerForm
          mode="edit"
          listing={oauthListing({ oauth: { authorized: false } })}
          onSubmit={noop}
          onCancel={noop}
          onAuthorize={onAuthorize}
        />,
      );

      fireEvent.click(screen.getByTestId("org-oauth-authorize-button"));

      await waitFor(() =>
        expect(screen.getByTestId("org-oauth-authorize-button")).toBeEnabled(),
      );
      expect(
        screen.queryByTestId("org-oauth-authorized"),
      ).not.toBeInTheDocument();
    });

    it("discards an authorization when the settings it ran with change", async () => {
      const onSubmit = vi.fn();
      const onAuthorize = vi
        .fn()
        .mockResolvedValue({ ok: true, jobId: "job-1" });
      render(
        <OrgMcpServerForm
          mode="edit"
          listing={oauthListing({ oauth: { authorized: false } })}
          onSubmit={onSubmit}
          onCancel={noop}
          onAuthorize={onAuthorize}
        />,
      );
      fireEvent.click(screen.getByTestId("org-oauth-authorize-button"));
      await screen.findByTestId("org-oauth-authorized");

      fill("org-oauth-scopes-input", "admin");

      expect(
        screen.queryByTestId("org-oauth-authorized"),
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it("keeps stored tokens and client secret on an unchanged edit", () => {
      const onSubmit = vi.fn();
      render(
        <OrgMcpServerForm
          mode="edit"
          listing={oauthListing()}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );
      expect(screen.getByTestId("org-oauth-authorized")).toBeInTheDocument();

      fireEvent.click(screen.getByTestId("org-submit-button"));

      expect(onSubmit).toHaveBeenCalledWith({
        name: "issue-tracker",
        managed_by: "org",
        server: {
          url: OAUTH_URL,
          transport: "http",
          auth: {
            strategy: "oauth2",
            authentication: {
              type: "oauth",
              client_id: "client-1",
              client_secret: REDACTED_MCP_SECRET,
              scopes: ["read", "write"],
            },
          },
        },
      });
    });

    it("asks for the client secret and a new authorization after a URL change", () => {
      const onSubmit = vi.fn();
      render(
        <OrgMcpServerForm
          mode="edit"
          listing={oauthListing()}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );

      fill("org-url-input", "https://elsewhere.example/mcp");
      expect(
        screen.queryByTestId("org-oauth-authorized"),
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
        "SETTINGS$MCP_ORG_ERROR_OAUTH_CLIENT_SECRET_REQUIRED_AFTER_URL_CHANGE",
      );

      fill("org-oauth-client-secret-input", "client-secret");
      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
        "SETTINGS$MCP_ORG_ERROR_OAUTH_AUTHORIZATION_REQUIRED",
      );
      expect(onSubmit).not.toHaveBeenCalled();
    });
  });

  it("requests a connection test with the form's current values", () => {
    const onTest = vi.fn();
    render(
      <OrgMcpServerForm
        mode="add"
        onSubmit={noop}
        onCancel={noop}
        onTest={onTest}
        testMessage={{ ok: true, text: "SETTINGS$MCP_TEST_SUCCESS" }}
      />,
    );
    fill("org-name-input", "bifrost");
    fill("org-url-input", BIFROST_URL);
    fill("org-timeout-input", "120");
    fill("org-secret-name-input", "BIFROST_TOKEN");

    fireEvent.click(screen.getByTestId("org-mcp-test-connection"));

    expect(onTest).toHaveBeenCalledWith({
      name: "bifrost",
      managed_by: "user",
      server: {
        url: BIFROST_URL,
        transport: "http",
        timeout: 120,
        auth: { strategy: "bearer", value: secretReference("BIFROST_TOKEN") },
      },
    });
    expect(screen.getByTestId("org-mcp-test-message")).toHaveTextContent(
      "SETTINGS$MCP_TEST_SUCCESS",
    );
  });
});
