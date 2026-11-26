import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { createRoutesStub, useParams } from "react-router";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { warmRuntimesService } from "#/api/warm-runtimes-service/warm-runtimes-service.api";
import { SuperAdminWarmRuntimes } from "#/components/features/super-admin/super-admin-warm-runtimes";
import translations from "#/i18n/translation.json";
import { CONFIGMAP_RUNTIME, LARGE_RUNTIME } from "./warm-runtime-fixtures";

function EditPathProbe() {
  const { name } = useParams<{ name: string }>();
  return <p data-testid="location">{name ?? "new"}</p>;
}

function PermissionsPathProbe() {
  const { name } = useParams<{ name: string }>();
  return <p data-testid="permissions-location">{name}</p>;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    { path: "/super-admin/warm-runtimes", Component: SuperAdminWarmRuntimes },
    { path: "/super-admin/warm-runtimes/new", Component: EditPathProbe },
    {
      path: "/super-admin/warm-runtimes/edit/:name",
      Component: EditPathProbe,
    },
    {
      path: "/super-admin/warm-runtimes/permissions/:name",
      Component: PermissionsPathProbe,
    },
  ]);
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <RouterStub initialEntries={["/super-admin/warm-runtimes"]} />
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

async function openRowMenu(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  await user.click(
    await screen.findByTestId(`super-admin-warm-runtime-actions-${name}`),
  );
}

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    resources: {
      en: {
        translation: {
          SUPER_ADMIN$DELETE_WARM_RUNTIME_CONFIRM:
            translations.SUPER_ADMIN$DELETE_WARM_RUNTIME_CONFIRM.en,
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

describe("Super Admin Warm Runtimes page", () => {
  beforeEach(() => {
    vi.spyOn(warmRuntimesService, "listWarmRuntimes").mockResolvedValue([
      CONFIGMAP_RUNTIME,
      LARGE_RUNTIME,
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists each runtime with its image, resources, timeout and source", async () => {
    renderPage();

    const largeRow = (await screen.findByText("large")).closest("tr")!;
    expect(
      within(largeRow).getByText("registry.example/custom:2"),
    ).toBeInTheDocument();
    expect(within(largeRow).getByText("2 · 4Gi")).toBeInTheDocument();
    expect(within(largeRow).getByText("900s")).toBeInTheDocument();
    expect(
      within(largeRow).getByText("SUPER_ADMIN$WARM_RUNTIME_SOURCE_DB"),
    ).toBeInTheDocument();

    const configMapRow = screen.getByText("v1_current").closest("tr")!;
    expect(
      within(configMapRow).getByText("SUPER_ADMIN$WARM_RUNTIME_SOURCE_FILE"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("super-admin-warm-runtimes-configmap-notice"),
    ).toBeInTheDocument();
  });

  it("hides the ConfigMap notice when every runtime was saved through the API", async () => {
    vi.spyOn(warmRuntimesService, "listWarmRuntimes").mockResolvedValue([
      LARGE_RUNTIME,
    ]);
    renderPage();

    await screen.findByText("large");

    expect(
      screen.queryByTestId("super-admin-warm-runtimes-configmap-notice"),
    ).not.toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    vi.spyOn(warmRuntimesService, "listWarmRuntimes").mockResolvedValue([]);
    renderPage();

    expect(
      await screen.findByText("SUPER_ADMIN$EMPTY_WARM_RUNTIMES"),
    ).toBeInTheDocument();
  });

  it("opens the create page from the add button", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByTestId("super-admin-add-warm-runtime"));

    expect(await screen.findByTestId("location")).toHaveTextContent("new");
  });

  it("opens the edit page for the chosen runtime", async () => {
    const user = userEvent.setup();
    renderPage();

    await openRowMenu(user, "large");
    await user.click(screen.getByTestId("super-admin-warm-runtime-edit-large"));

    expect(await screen.findByTestId("location")).toHaveTextContent("large");
  });

  it("shows which runtimes are private", async () => {
    renderPage();

    const largeRow = (await screen.findByText("large")).closest("tr")!;
    const configMapRow = screen.getByText("v1_current").closest("tr")!;

    expect(
      within(largeRow).getByText("SUPER_ADMIN$WARM_RUNTIME_PRIVATE"),
    ).toBeInTheDocument();
    expect(
      within(configMapRow).getByText("SUPER_ADMIN$WARM_RUNTIME_ACCESS_PUBLIC"),
    ).toBeInTheDocument();
  });

  it("opens the permissions page for the chosen runtime", async () => {
    const user = userEvent.setup();
    renderPage();

    await openRowMenu(user, "large");
    await user.click(
      screen.getByTestId("super-admin-warm-runtime-permissions-large"),
    );

    expect(await screen.findByTestId("permissions-location")).toHaveTextContent(
      "large",
    );
  });

  it("deletes a runtime after confirmation", async () => {
    const user = userEvent.setup();
    const deleteRuntime = vi
      .spyOn(warmRuntimesService, "deleteWarmRuntime")
      .mockResolvedValue();
    renderPage();

    await openRowMenu(user, "large");
    await user.click(
      screen.getByTestId("super-admin-warm-runtime-delete-large"),
    );
    const dialog = screen.getByTestId(
      "super-admin-warm-runtime-delete-confirm",
    );
    expect(within(dialog).getByText("large")).toBeInTheDocument();
    await user.click(
      screen.getByTestId("super-admin-warm-runtime-delete-confirm-submit"),
    );

    await waitFor(() =>
      expect(deleteRuntime).toHaveBeenCalledExactlyOnceWith({ name: "large" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByTestId("super-admin-warm-runtime-delete-confirm"),
      ).not.toBeInTheDocument(),
    );
  });

  it("does not delete when the confirmation is cancelled", async () => {
    const user = userEvent.setup();
    const deleteRuntime = vi.spyOn(warmRuntimesService, "deleteWarmRuntime");
    renderPage();

    await openRowMenu(user, "large");
    await user.click(
      screen.getByTestId("super-admin-warm-runtime-delete-large"),
    );
    await user.click(
      screen.getByTestId("super-admin-warm-runtime-delete-confirm-cancel"),
    );

    expect(deleteRuntime).not.toHaveBeenCalled();
    expect(
      screen.queryByTestId("super-admin-warm-runtime-delete-confirm"),
    ).not.toBeInTheDocument();
  });

  it("does not offer deleting a ConfigMap runtime", async () => {
    const user = userEvent.setup();
    renderPage();

    await openRowMenu(user, "v1_current");

    expect(
      screen.getByTestId("super-admin-warm-runtime-delete-v1_current"),
    ).toBeDisabled();
  });
});
