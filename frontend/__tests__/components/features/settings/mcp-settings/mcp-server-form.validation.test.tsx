import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { MCPServerForm } from "#/components/features/settings/mcp-settings/mcp-server-form";

// i18n mock
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

    // Fill required fields
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

    // Should show invalid env format error
    expect(
      screen.getByText("SETTINGS$MCP_ERROR_ENV_INVALID_FORMAT"),
    ).toBeInTheDocument();

    // Fix env with valid lines and blank lines
    fireEvent.change(screen.getByTestId("env-input"), {
      target: { value: "KEY=value\n\nANOTHER=123" },
    });

    fireEvent.click(screen.getByTestId("submit-button"));

    // No error; submit should be called
    expect(onSubmit).toHaveBeenCalledTimes(1);
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

    // Unmount first form, then check shttp duplicate
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

  describe("server names", () => {
    const existingServers = [
      { id: "stdio-0", type: "stdio" as const, name: "github", command: "npx" },
      {
        id: "shttp-0",
        type: "shttp" as const,
        name: "shttp",
        url: "https://tools.example.com/jira-mcp/mcp",
      },
    ];

    it("suggests a remote server's name from its URL and submits it", () => {
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

      fireEvent.change(screen.getByTestId("url-input"), {
        target: { value: "https://tools.example.com/sonarqube-mcp/mcp" },
      });
      expect(screen.getByTestId("name-input")).toHaveValue("sonarqube");

      fireEvent.click(screen.getByTestId("submit-button"));
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "shttp",
          name: "sonarqube",
          url: "https://tools.example.com/sonarqube-mcp/mcp",
        }),
      );
    });

    it("keeps a typed name when the URL changes", () => {
      render(
        <MCPServerForm
          mode="add"
          server={{ id: "tmp", type: "sse" }}
          existingServers={existingServers}
          onSubmit={noop}
          onCancel={noop}
        />,
      );

      fireEvent.change(screen.getByTestId("name-input"), {
        target: { value: "code-quality" },
      });
      fireEvent.change(screen.getByTestId("url-input"), {
        target: { value: "https://tools.example.com/sonarqube-mcp/sse" },
      });
      expect(screen.getByTestId("name-input")).toHaveValue("code-quality");
    });

    it("rejects invalid characters in a remote server's name", () => {
      const onSubmit = vi.fn();
      render(
        <MCPServerForm
          mode="add"
          server={{ id: "tmp", type: "shttp" }}
          existingServers={[]}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );

      fireEvent.change(screen.getByTestId("url-input"), {
        target: { value: "https://tools.example.com/mcp" },
      });
      fireEvent.change(screen.getByTestId("name-input"), {
        target: { value: "sonar qube" },
      });
      fireEvent.submit(screen.getByTestId("add-mcp-server-form"));

      expect(
        screen.getByText("SETTINGS$MCP_ERROR_NAME_INVALID"),
      ).toBeInTheDocument();
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it.each([
      ["a remote server reusing a STDIO name", "shttp", "GitHub"],
      ["a STDIO server reusing a remote name", "stdio", "shttp"],
    ] as const)("rejects %s", (_label, type, name) => {
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

      expect(
        screen.getByText("SETTINGS$MCP_ERROR_NAME_DUPLICATE"),
      ).toBeInTheDocument();
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it("suggests a name that is not taken", () => {
      render(
        <MCPServerForm
          mode="add"
          server={{ id: "tmp", type: "shttp" }}
          existingServers={[
            ...existingServers,
            {
              id: "shttp-1",
              type: "shttp" as const,
              name: "sonarqube",
              url: "https://a.example.com/sonarqube-mcp/mcp",
            },
          ]}
          onSubmit={noop}
          onCancel={noop}
        />,
      );

      fireEvent.change(screen.getByTestId("url-input"), {
        target: { value: "https://b.example.com/sonarqube-mcp/mcp" },
      });
      expect(screen.getByTestId("name-input")).toHaveValue("sonarqube-2");
    });

    it("renames a server on edit without flagging its own name", () => {
      const onSubmit = vi.fn();
      const view = render(
        <MCPServerForm
          mode="edit"
          server={existingServers[1]}
          existingServers={existingServers}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );

      expect(screen.getByTestId("name-input")).toHaveValue("shttp");
      fireEvent.click(screen.getByTestId("submit-button"));
      expect(onSubmit).toHaveBeenLastCalledWith(
        expect.objectContaining({ id: "shttp-0", name: "shttp" }),
      );

      fireEvent.change(screen.getByTestId("name-input"), {
        target: { value: "jira" },
      });
      fireEvent.click(screen.getByTestId("submit-button"));
      expect(onSubmit).toHaveBeenLastCalledWith(
        expect.objectContaining({ id: "shttp-0", name: "jira" }),
      );
      view.unmount();
    });

    it("keeps an unchanged stored name that predates the naming rules", () => {
      const onSubmit = vi.fn();
      render(
        <MCPServerForm
          mode="edit"
          server={{
            id: "shttp-0",
            type: "shttp",
            name: "team.sonar",
            url: "https://tools.example.com/sonarqube-mcp/mcp",
          }}
          existingServers={[]}
          onSubmit={onSubmit}
          onCancel={noop}
        />,
      );

      fireEvent.click(screen.getByTestId("submit-button"));
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ name: "team.sonar" }),
      );
    });

    it("suggests a name for a legacy remote server saved without one", () => {
      render(
        <MCPServerForm
          mode="edit"
          server={{
            id: "sse-0",
            type: "sse",
            url: "https://mcp.linear.app/sse",
          }}
          existingServers={[]}
          onSubmit={noop}
          onCancel={noop}
        />,
      );

      expect(screen.getByTestId("name-input")).toHaveValue("linear");
    });
  });
});
