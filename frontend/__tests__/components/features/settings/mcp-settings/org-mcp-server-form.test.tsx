import { fireEvent, render, screen } from "@testing-library/react";
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
