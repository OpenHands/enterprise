import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { warmRuntimesService } from "#/api/warm-runtimes-service/warm-runtimes-service.api";
import { SuperAdminWarmRuntimeEdit } from "#/components/features/super-admin/super-admin-warm-runtime-edit";
import { CONFIGMAP_RUNTIME, LARGE_RUNTIME } from "./warm-runtime-fixtures";

function renderPage(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin/warm-runtimes",
      Component: () => <p data-testid="list-page" />,
    },
    {
      path: "/super-admin/warm-runtimes/new",
      Component: SuperAdminWarmRuntimeEdit,
    },
    {
      path: "/super-admin/warm-runtimes/edit/:name",
      Component: SuperAdminWarmRuntimeEdit,
    },
  ]);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterStub initialEntries={[path]} />
    </QueryClientProvider>,
  );
}

const input = (suffix: string) =>
  screen.getByTestId<HTMLInputElement>(`super-admin-warm-runtime-${suffix}`);

const submitButton = () =>
  screen.getByTestId("super-admin-warm-runtime-form-submit");

describe("Super Admin warm runtime form", () => {
  beforeEach(() => {
    vi.spyOn(warmRuntimesService, "listWarmRuntimes").mockResolvedValue([
      CONFIGMAP_RUNTIME,
      LARGE_RUNTIME,
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates a runtime from the filled-in form and returns to the list", async () => {
    const user = userEvent.setup();
    const createRuntime = vi
      .spyOn(warmRuntimesService, "createWarmRuntime")
      .mockResolvedValue({ ...LARGE_RUNTIME, name: "big" });
    renderPage("/super-admin/warm-runtimes/new");

    await screen.findByTestId("super-admin-warm-runtime-form");
    expect(submitButton()).toBeDisabled();

    await user.type(input("name"), "big");
    await user.type(input("image"), "img:1");
    await user.type(input("environment"), "A=1{Enter}B=2");
    await user.type(input("memory-limit"), "8Gi");
    await user.type(input("idle-seconds"), "600");
    await user.click(submitButton());

    await waitFor(() =>
      expect(createRuntime).toHaveBeenCalledExactlyOnceWith({
        name: "big",
        image: "img:1",
        working_dir: "/home/openhands/workspace",
        command: [],
        args: [],
        environment: { A: "1", B: "2" },
        resources: { requests: null, limits: { memory: "8Gi" } },
        idle_seconds: 600,
        persistent_storage_size: null,
        count: null,
        run_as_user: null,
        run_as_group: null,
        fs_group: null,
        fuse_s3_mount: null,
      }),
    );
    expect(await screen.findByTestId("list-page")).toBeInTheDocument();
  });

  it("stays on the form when creation fails", async () => {
    const user = userEvent.setup();
    const createRuntime = vi
      .spyOn(warmRuntimesService, "createWarmRuntime")
      .mockRejectedValue(new Error("conflict"));
    renderPage("/super-admin/warm-runtimes/new");

    await screen.findByTestId("super-admin-warm-runtime-form");
    await user.type(input("name"), "big");
    await user.type(input("image"), "img:1");
    await user.click(submitButton());

    await waitFor(() => expect(createRuntime).toHaveBeenCalled());
    expect(screen.queryByTestId("list-page")).not.toBeInTheDocument();
    expect(screen.getByTestId("super-admin-warm-runtime-form")).toBeVisible();
  });

  it("explains a malformed name and environment line and blocks saving", async () => {
    const user = userEvent.setup();
    renderPage("/super-admin/warm-runtimes/new");

    await screen.findByTestId("super-admin-warm-runtime-form");
    await user.type(input("name"), "bad name");
    await user.type(input("image"), "img:1");
    await user.type(input("environment"), "oops");

    expect(
      screen.getByText("SUPER_ADMIN$WARM_RUNTIME_NAME_INVALID"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("SUPER_ADMIN$WARM_RUNTIME_ENVIRONMENT_INVALID"),
    ).toBeInTheDocument();
    expect(submitButton()).toBeDisabled();
  });

  it("loads an existing runtime, locks its name and saves the changes", async () => {
    const user = userEvent.setup();
    const updateRuntime = vi
      .spyOn(warmRuntimesService, "updateWarmRuntime")
      .mockResolvedValue({ ...LARGE_RUNTIME, idle_seconds: 60 });
    renderPage("/super-admin/warm-runtimes/edit/large");

    await screen.findByTestId("super-admin-warm-runtime-form");
    expect(input("name")).toBeDisabled();
    expect(input("name").value).toBe("large");
    expect(input("image").value).toBe("registry.example/custom:2");
    expect(input("command").value).toBe("agent-server\n--verbose");
    expect(input("cpu-limit").value).toBe("2");
    expect(input("idle-seconds").value).toBe("900");
    expect(submitButton()).toBeDisabled();

    await user.clear(input("idle-seconds"));
    await user.type(input("idle-seconds"), "60");
    await user.click(submitButton());

    const { name, source, ...fields } = LARGE_RUNTIME;
    expect(source).toBe("db");
    await waitFor(() =>
      expect(updateRuntime).toHaveBeenCalledExactlyOnceWith({
        name,
        ...fields,
        idle_seconds: 60,
      }),
    );
  });

  it("tells the admin that saving a ConfigMap runtime stores an override", async () => {
    renderPage("/super-admin/warm-runtimes/edit/v1_current");

    expect(
      await screen.findByTestId("super-admin-warm-runtime-configmap-hint"),
    ).toBeInTheDocument();
  });

  it("reports a runtime that does not exist", async () => {
    renderPage("/super-admin/warm-runtimes/edit/ghost");

    expect(
      await screen.findByTestId("super-admin-warm-runtime-not-found"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("super-admin-warm-runtime-form"),
    ).not.toBeInTheDocument();
  });
});
