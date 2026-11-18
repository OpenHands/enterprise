import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MCPServerForm } from "#/components/features/settings/mcp-settings/mcp-server-form";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

describe("MCPServerForm validation", () => {
  const noop = () => {};

  it("rejects invalid env var lines and allows blank lines", () => {
    const onSubmit = vi.fn();

    render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp", type: "stdio" }}
        existingServers={[]}
        onSubmit={onSubmit}
        onCancel={noop}
      />,
    );

    fireEvent.change(screen.getByTestId("name-input"), {
      target: { value: "my-server" },
    });
    fireEvent.change(screen.getByTestId("command-input"), {
      target: { value: "npx" },
    });

    // Invalid env entries mixed with blank lines
    fireEvent.change(screen.getByTestId("env-input"), {
      target: { value: "invalid\n\nKEY=value\n=novalue\nKEY_ONLY=" },
    });

    fireEvent.click(screen.getByTestId("submit-button"));

    expect(
      screen.getByText("SETTINGS$MCP_ERROR_ENV_INVALID_FORMAT"),
    ).toBeInTheDocument();

    // Fix env with valid lines and blank lines
    fireEvent.change(screen.getByTestId("env-input"), {
      target: { value: "KEY=value\n\nANOTHER=123" },
    });

    fireEvent.click(screen.getByTestId("submit-button"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("rejects a name the organization already provides an MCP server under", () => {
    const onSubmit = vi.fn();

    render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp", type: "stdio" }}
        existingServers={[]}
        reservedNames={["bifrost-internal"]}
        onSubmit={onSubmit}
        onCancel={noop}
      />,
    );

    fireEvent.change(screen.getByTestId("name-input"), {
      target: { value: "bifrost-internal" },
    });
    fireEvent.change(screen.getByTestId("command-input"), {
      target: { value: "npx" },
    });
    fireEvent.click(screen.getByTestId("submit-button"));

    // A personal server of that name would hide the organization's.
    expect(
      screen.getByText("SETTINGS$MCP_ERROR_NAME_RESERVED_BY_ORG"),
    ).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("rejects duplicate URLs across sse/shttp types", () => {
    const onSubmit = vi.fn();

    const existingServers = [
      { id: "sse-1", type: "sse" as const, url: "https://api.example.com" },
      { id: "shttp-1", type: "shttp" as const, url: "https://x.example.com" },
    ];

    const r1 = render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp", type: "sse" }}
        existingServers={existingServers}
        onSubmit={onSubmit}
        onCancel={noop}
      />,
    );

    fireEvent.change(screen.getAllByTestId("url-input")[0], {
      target: { value: "https://api.example.com" },
    });

    fireEvent.click(screen.getAllByTestId("submit-button")[0]);
    expect(
      screen.getByText("SETTINGS$MCP_ERROR_URL_DUPLICATE"),
    ).toBeInTheDocument();

    r1.unmount();

    const r2 = render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp2", type: "shttp" }}
        existingServers={existingServers}
        onSubmit={onSubmit}
        onCancel={noop}
      />,
    );

    fireEvent.change(screen.getAllByTestId("url-input")[0], {
      target: { value: "https://api.example.com" },
    });

    fireEvent.click(screen.getAllByTestId("submit-button")[0]);
    expect(
      screen.getByText("SETTINGS$MCP_ERROR_URL_DUPLICATE"),
    ).toBeInTheDocument();

    r2.unmount();
  });

  it("offers a connection test for remote servers only", () => {
    const stdio = render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp", type: "stdio" }}
        onSubmit={noop}
        onCancel={noop}
        onTest={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("mcp-test-connection")).not.toBeInTheDocument();
    stdio.unmount();

    render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp", type: "sse" }}
        onSubmit={noop}
        onCancel={noop}
        onTest={vi.fn()}
      />,
    );
    expect(screen.getByTestId("mcp-test-connection")).toBeInTheDocument();
  });

  it("does not request a connection test while the form is invalid", () => {
    const onTest = vi.fn();
    render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp", type: "sse" }}
        onSubmit={noop}
        onCancel={noop}
        onTest={onTest}
      />,
    );

    fireEvent.click(screen.getByTestId("mcp-test-connection"));

    expect(
      screen.getByText("SETTINGS$MCP_ERROR_URL_REQUIRED"),
    ).toBeInTheDocument();
    expect(onTest).not.toHaveBeenCalled();
  });

  it("disables the connection test and submit buttons while a test is pending", () => {
    render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp", type: "sse" }}
        onSubmit={noop}
        onCancel={noop}
        onTest={vi.fn()}
        isTestPending
      />,
    );

    expect(screen.getByTestId("mcp-test-connection")).toBeDisabled();
    expect(screen.getByTestId("mcp-test-connection")).toHaveTextContent(
      "SETTINGS$MCP_TESTING",
    );
    expect(screen.getByTestId("submit-button")).toBeDisabled();
  });

  it("requests a connection test with the form's current values", () => {
    const onTest = vi.fn();
    render(
      <MCPServerForm
        mode="add"
        server={{ id: "tmp", type: "shttp" }}
        onSubmit={noop}
        onCancel={noop}
        onTest={onTest}
        testMessage={{ ok: true, text: "connected" }}
      />,
    );

    fireEvent.change(screen.getByTestId("url-input"), {
      target: { value: "https://mcp.example.com/mcp" },
    });
    fireEvent.change(screen.getByTestId("api-key-input"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByTestId("mcp-test-connection"));

    expect(onTest).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "shttp",
        url: "https://mcp.example.com/mcp",
        api_key: "secret",
      }),
    );
    expect(screen.getByTestId("mcp-test-message")).toHaveTextContent(
      "connected",
    );
  });

  describe("remote server names", () => {
    const existingServers = [
      { id: "stdio-0", type: "stdio" as const, name: "github", command: "npx" },
      {
        id: "shttp-0",
        type: "shttp" as const,
        name: "shttp",
        url: "https://tools.example.com/jira-mcp/mcp",
      },
    ];

    it("submits a remote server under the name typed in the form", () => {
      // Arrange
      const onSubmit = vi.fn();
      render(
        <MCPServerForm
          mode="add"
          server={{ id: "tmp", type: "shttp" }}
          existingServers={existingServers}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );

      // Act
      fireEvent.change(screen.getByTestId("name-input"), {
        target: { value: "sonarqube" },
      });
      fireEvent.change(screen.getByTestId("url-input"), {
        target: { value: "https://tools.example.com/sonarqube-mcp/mcp" },
      });
      fireEvent.click(screen.getByTestId("submit-button"));

      // Assert
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "shttp",
          name: "sonarqube",
          url: "https://tools.example.com/sonarqube-mcp/mcp",
        }),
      );
    });

    it.each([
      ["a remote server reusing a STDIO server's name", "shttp", "GitHub"],
      ["a STDIO server reusing a remote server's name", "stdio", "SHTTP"],
    ] as const)("rejects %s, ignoring case", (_label, type, name) => {
      // Arrange
      const onSubmit = vi.fn();
      render(
        <MCPServerForm
          mode="add"
          server={{ id: "tmp", type }}
          existingServers={existingServers}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );

      // Act
      if (type === "stdio") {
        fireEvent.change(screen.getByTestId("command-input"), {
          target: { value: "npx" },
        });
      } else {
        fireEvent.change(screen.getByTestId("url-input"), {
          target: { value: "https://tools.example.com/other/mcp" },
        });
      }
      fireEvent.change(screen.getByTestId("name-input"), {
        target: { value: name },
      });
      fireEvent.click(screen.getByTestId("submit-button"));

      // Assert
      expect(
        screen.getByText("SETTINGS$MCP_ERROR_NAME_DUPLICATE"),
      ).toBeInTheDocument();
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it("rejects a remote server name the organization already provides", () => {
      // Arrange
      const onSubmit = vi.fn();
      render(
        <MCPServerForm
          mode="add"
          server={{ id: "tmp", type: "shttp" }}
          existingServers={[]}
          reservedNames={["bifrost-internal"]}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );

      // Act
      fireEvent.change(screen.getByTestId("name-input"), {
        target: { value: "bifrost-internal" },
      });
      fireEvent.change(screen.getByTestId("url-input"), {
        target: { value: "https://bifrost.example.com/mcp" },
      });
      fireEvent.click(screen.getByTestId("submit-button"));

      // Assert
      expect(
        screen.getByText("SETTINGS$MCP_ERROR_NAME_RESERVED_BY_ORG"),
      ).toBeInTheDocument();
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it("renames a remote server on edit without flagging its own name", () => {
      // Arrange
      const onSubmit = vi.fn();
      render(
        <MCPServerForm
          mode="edit"
          server={existingServers[1]}
          existingServers={existingServers}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );
      expect(screen.getByTestId("name-input")).toHaveValue("shttp");

      // Act
      fireEvent.click(screen.getByTestId("submit-button"));
      fireEvent.change(screen.getByTestId("name-input"), {
        target: { value: "jira" },
      });
      fireEvent.click(screen.getByTestId("submit-button"));

      // Assert
      expect(onSubmit).toHaveBeenNthCalledWith(
        1,
        expect.objectContaining({ id: "shttp-0", name: "shttp" }),
      );
      expect(onSubmit).toHaveBeenNthCalledWith(
        2,
        expect.objectContaining({ id: "shttp-0", name: "jira" }),
      );
    });
  });
});
