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

async function chooseStdio() {
  await userEvent.click(screen.getByTestId("org-server-type-dropdown"));
  await userEvent.click(
    await screen.findByRole("option", {
      name: "SETTINGS$MCP_SERVER_TYPE_STDIO",
    }),
  );
}

function storedStdioListing(): OrgMcpServerView {
  return {
    name: "slack",
    managedBy: "user",
    type: "stdio",
    command: "npx",
    args: ["-y", "@slack/mcp"],
    env: { SLACK_BOT_TOKEN: REDACTED_MCP_SECRET },
    auth: "none",
    secretNames: ["SLACK_TOKEN"],
    server: {},
  };
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

  it("asks for the authentication method before the credential source and the personal secret name", () => {
    render(<OrgMcpServerForm mode="add" onSubmit={noop} onCancel={noop} />);

    const fieldOrder = Array.from(
      screen
        .getByTestId("add-org-mcp-server-form")
        .querySelectorAll("input[data-testid]"),
    ).map((input) => input.getAttribute("data-testid"));

    expect(fieldOrder).toEqual([
      "org-server-type-dropdown",
      "org-name-input",
      "org-url-input",
      "org-timeout-input",
      "org-auth-dropdown",
      "org-managed-by-dropdown",
      "org-secret-name-input",
    ]);
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
      expect(
        screen.getByText("SETTINGS$MCP_ORG_OAUTH_USER_HELP"),
      ).toBeInTheDocument();
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

  describe("stdio", () => {
    it("adds a stdio listing with its command, arguments and environment variables", async () => {
      const onSubmit = vi.fn();
      render(
        <OrgMcpServerForm
          mode="add"
          onSubmit={onSubmit}
          onCancel={noop}
          onTest={vi.fn()}
        />,
      );

      await chooseStdio();
      fill("org-name-input", "slack");
      fill("org-command-input", "npx");
      fill("org-args-input", "-y\n@slack/mcp\n");
      fill(
        "org-env-input",
        `SLACK_BOT_TOKEN=${secretReference("SLACK_TOKEN")}\n\nSLACK_TEAM_ID = T123`,
      );
      fireEvent.click(screen.getByTestId("org-submit-button"));

      expect(onSubmit).toHaveBeenCalledWith({
        name: "slack",
        managed_by: "user",
        server: {
          command: "npx",
          args: ["-y", "@slack/mcp"],
          env: {
            SLACK_BOT_TOKEN: secretReference("SLACK_TOKEN"),
            SLACK_TEAM_ID: "T123",
          },
        },
      });
      // No URL or credential fields, and no connection test: a stdio server
      // only runs inside the conversation sandbox.
      expect(screen.queryByTestId("org-url-input")).not.toBeInTheDocument();
      expect(screen.queryByTestId("org-auth-dropdown")).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("org-mcp-test-connection"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText("SETTINGS$MCP_ORG_ENV_USER_HELP"),
      ).toBeInTheDocument();
    });

    it("rejects a command with spaces or a malformed environment line", async () => {
      const onSubmit = vi.fn();
      render(
        <OrgMcpServerForm mode="add" onSubmit={onSubmit} onCancel={noop} />,
      );
      await chooseStdio();
      fill("org-name-input", "slack");

      fill("org-command-input", "npx -y");
      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
        "SETTINGS$MCP_ERROR_COMMAND_NO_SPACES",
      );

      fill("org-command-input", "npx");
      fill("org-env-input", "SLACK_BOT_TOKEN");
      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
        "SETTINGS$MCP_ERROR_ENV_INVALID_FORMAT",
      );
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it("keeps a stored environment while editing unless the command or arguments change", () => {
      const onSubmit = vi.fn();
      render(
        <OrgMcpServerForm
          mode="edit"
          listing={storedStdioListing()}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );
      expect(screen.getByTestId("org-env-input")).toHaveValue(
        `SLACK_BOT_TOKEN=${REDACTED_MCP_SECRET}`,
      );

      // Masked values go back as they are: the backend restores the stored ones.
      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(onSubmit).toHaveBeenLastCalledWith({
        name: "slack",
        managed_by: "user",
        server: {
          command: "npx",
          args: ["-y", "@slack/mcp"],
          env: { SLACK_BOT_TOKEN: REDACTED_MCP_SECRET },
        },
      });

      // New arguments need the values again: the backend binds a stored
      // environment to the command it was entered for.
      fill("org-args-input", "-y\n@slack/mcp@2");
      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(screen.getByTestId("org-mcp-form-error")).toHaveTextContent(
        "SETTINGS$MCP_ORG_ERROR_ENV_REQUIRED_AFTER_COMMAND_CHANGE",
      );
      expect(onSubmit).toHaveBeenCalledTimes(1);

      fill(
        "org-env-input",
        `SLACK_BOT_TOKEN=${secretReference("SLACK_TOKEN")}`,
      );
      fireEvent.click(screen.getByTestId("org-submit-button"));
      expect(onSubmit).toHaveBeenLastCalledWith({
        name: "slack",
        managed_by: "user",
        server: {
          command: "npx",
          args: ["-y", "@slack/mcp@2"],
          env: { SLACK_BOT_TOKEN: secretReference("SLACK_TOKEN") },
        },
      });
    });
  });

  it("requests a connection test with the form's current values", () => {
    const onTest = vi.fn();
    render(
      <OrgMcpServerForm
        mode="edit"
        listing={storedOrgListing()}
        onSubmit={noop}
        onCancel={noop}
        onTest={onTest}
        testMessage={{ ok: true, text: "SETTINGS$MCP_TEST_SUCCESS" }}
      />,
    );
    fill("org-timeout-input", "120");
    fill("org-token-input", "rotated-key");

    fireEvent.click(screen.getByTestId("org-mcp-test-connection"));

    expect(onTest).toHaveBeenCalledWith({
      name: "datadog-shared",
      managed_by: "org",
      server: {
        url: DATADOG_URL,
        transport: "http",
        timeout: 120,
        auth: { strategy: "bearer", value: "rotated-key" },
      },
    });
    expect(screen.getByTestId("org-mcp-test-message")).toHaveTextContent(
      "SETTINGS$MCP_TEST_SUCCESS",
    );
  });

  it("offers no connection test for a user-managed listing that uses each member's credential", () => {
    // The form opens as a user-managed bearer listing.
    render(
      <OrgMcpServerForm
        mode="add"
        onSubmit={noop}
        onCancel={noop}
        onTest={vi.fn()}
        testMessage={{ ok: true, text: "SETTINGS$MCP_TEST_SUCCESS" }}
      />,
    );

    expect(
      screen.queryByTestId("org-mcp-test-connection"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("org-mcp-test-message"),
    ).not.toBeInTheDocument();
  });

  it("keeps the connection test for a user-managed listing without a credential", () => {
    const onTest = vi.fn();
    render(
      <OrgMcpServerForm
        mode="edit"
        listing={{
          name: "docs-search",
          managedBy: "user",
          type: "shttp",
          url: BIFROST_URL,
          auth: "none",
          secretNames: [],
          server: {},
        }}
        onSubmit={noop}
        onCancel={noop}
        onTest={onTest}
      />,
    );

    fireEvent.click(screen.getByTestId("org-mcp-test-connection"));

    expect(onTest).toHaveBeenCalledWith({
      name: "docs-search",
      managed_by: "user",
      server: { url: BIFROST_URL, transport: "http" },
    });
  });
});
