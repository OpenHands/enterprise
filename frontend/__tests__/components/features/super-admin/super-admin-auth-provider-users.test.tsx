import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18n from "i18next";
import { createRoutesStub } from "react-router";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { authProvidersService } from "#/api/auth-providers-service/auth-providers-service.api";
import type {
  AuthProvider,
  AuthProviderUserLinkPage,
} from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";
import {
  superAdminService,
  type SuperAdminApiUser,
} from "#/api/super-admin-service/super-admin-service.api";
import { SuperAdminAuthProviderUsers } from "#/components/features/super-admin/super-admin-auth-provider-users";
import translations from "#/i18n/translation.json";

const GITHUB_PROVIDER: AuthProvider = {
  id: 1,
  kind: "OAuthProviderDetails",
  title: "GitHub",
  is_idp: false,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: {
    kind: "OAuthProviderDetails",
    provider_type: "github",
    client_id: "github-client-id",
    client_secret: "**********",
    authorization_url: null,
    token_url: null,
    userinfo_url: null,
    scopes: ["repo"],
    permitted_drift_seconds: 60,
    legacy_keycloak_type: null,
  },
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

const USER_PAGE: AuthProviderUserLinkPage = {
  items: [
    {
      id: 10,
      user_id: "user-1",
      email: "alice@example.com",
      external_subject_id: "gh-alice",
      external_email: "alice@old-email.com",
    },
    {
      id: 11,
      user_id: "user-2",
      email: "bob@example.com",
      external_subject_id: "gh-bob",
      external_email: null,
    },
  ],
  total: 2,
  current_page: 1,
  per_page: 10,
};

const DIRECTORY_USERS: SuperAdminApiUser[] = [
  {
    user_id: "user-1",
    email: "alice@example.com",
    name: "Alice",
    memberships: [],
    status: "active",
  },
  {
    user_id: "user-2",
    email: "bob@example.com",
    name: "Bob",
    memberships: [],
    status: "active",
  },
  {
    user_id: "user-3",
    email: "carol@example.com",
    name: "Carol",
    memberships: [],
    status: "active",
  },
  {
    // Pending invitation: no account to point a link at yet.
    user_id: null,
    email: "pending@example.com",
    name: null,
    memberships: [],
    status: "active",
  },
];

function renderPage(providerId = "1") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const RouterStub = createRoutesStub([
    {
      path: "/super-admin/auth-providers/:providerId/users",
      Component: SuperAdminAuthProviderUsers,
    },
    { path: "/super-admin/auth-providers", Component: () => null },
  ]);
  render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={queryClient}>
        <RouterStub
          initialEntries={[`/super-admin/auth-providers/${providerId}/users`]}
        />
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

// The subtitle names the provider through i18next interpolation, which
// only resolves `{{title}}` with a real i18n instance.
beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    resources: {
      en: {
        translation: {
          SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_SUBTITLE:
            translations.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_SUBTITLE.en,
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

describe("Super Admin Auth Provider User Links page", () => {
  beforeEach(() => {
    vi.spyOn(authProvidersService, "listAuthProviders").mockResolvedValue([
      GITHUB_PROVIDER,
    ]);
    vi.spyOn(authProvidersService, "listAuthProviderUsers").mockResolvedValue(
      USER_PAGE,
    );
    vi.spyOn(superAdminService, "listUsers").mockResolvedValue(DIRECTORY_USERS);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists the provider's user links by linked user and external identity", async () => {
    renderPage();

    expect(
      await screen.findByText("Identity links to accounts for GitHub."),
    ).toBeInTheDocument();
    expect(screen.getByText("alice@example.com (user-1)")).toBeInTheDocument();
    expect(
      screen.getByText("alice@old-email.com (gh-alice)"),
    ).toBeInTheDocument();
    expect(screen.getByText("bob@example.com (user-2)")).toBeInTheDocument();
    expect(screen.getByText("gh-bob")).toBeInTheDocument();
  });

  it("re-points a link to a different, already-existing user", async () => {
    const user = userEvent.setup();
    const updateAuthProviderUser = vi
      .spyOn(authProvidersService, "updateAuthProviderUser")
      .mockResolvedValue({
        id: 10,
        user_id: "user-3",
        email: "carol@example.com",
        external_subject_id: "gh-alice",
        external_email: "alice@old-email.com",
      });
    renderPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-user-edit-10"),
    );

    const dialog = screen.getByTestId(
      "super-admin-auth-provider-user-edit-modal",
    );

    // The pending invitation has no account yet, so it's not a selectable
    // target.
    await user.click(within(dialog).getByTestId("dropdown-trigger"));
    expect(
      screen.queryByRole("option", { name: "pending@example.com" }),
    ).not.toBeInTheDocument();

    await user.type(within(dialog).getByRole("combobox"), "carol");
    await user.click(
      screen.getByRole("option", { name: "carol@example.com (user-3)" }),
    );

    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-user-edit-confirm"),
    );

    await waitFor(() =>
      expect(updateAuthProviderUser).toHaveBeenCalledWith({
        providerId: 1,
        linkId: 10,
        userId: "user-3",
      }),
    );
  });

  it("allows re-pointing a link onto the signed-in admin's own account", async () => {
    // The same person can end up with more than one `User` row -- e.g. one
    // created with a password, another auto-created the first time they
    // signed in through this provider because the emails didn't match.
    // Nothing should stop them from re-pointing the link onto whichever
    // account they're currently signed in as, so the dropdown must not
    // exclude any directory user.
    const updateAuthProviderUser = vi
      .spyOn(authProvidersService, "updateAuthProviderUser")
      .mockResolvedValue({
        id: 11,
        user_id: "user-1",
        email: "alice@example.com",
        external_subject_id: "gh-bob",
        external_email: null,
      });
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-user-edit-11"),
    );
    const dialog = screen.getByTestId(
      "super-admin-auth-provider-user-edit-modal",
    );
    await user.click(within(dialog).getByTestId("dropdown-trigger"));

    const aliceOption = await screen.findByRole("option", {
      name: "alice@example.com (user-1)",
    });
    expect(aliceOption).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "bob@example.com (user-2)" }),
    ).toBeInTheDocument();

    await user.click(aliceOption);
    await user.click(
      within(dialog).getByTestId("super-admin-auth-provider-user-edit-confirm"),
    );

    await waitFor(() =>
      expect(updateAuthProviderUser).toHaveBeenCalledWith({
        providerId: 1,
        linkId: 11,
        userId: "user-1",
      }),
    );
  });

  it("only fetches the user directory once the re-point dialog is opened", async () => {
    const listUsers = vi.spyOn(superAdminService, "listUsers");
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Identity links to accounts for GitHub.");
    expect(listUsers).not.toHaveBeenCalled();

    await user.click(
      screen.getByTestId("super-admin-auth-provider-user-edit-10"),
    );

    await waitFor(() => expect(listUsers).toHaveBeenCalledTimes(1));
  });
});

// These tests don't `vi.spyOn` any service method, so every request -- the
// provider list, the user-links page, and the "re-point to" dropdown's
// directory lookup -- goes through the real MSW handlers in `src/mocks/`
// (the same ones `npm run dev:mock:saas` uses). The service-layer-mocked
// tests above can't catch a missing handler for a new endpoint; this is
// what actually exercises that path end to end.
describe("Super Admin Auth Provider User Links page (network-level MSW handlers)", () => {
  it("loads the user-links table and the re-point dropdown's options over the network", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(
      await screen.findByText("Identity links to accounts for GitHub."),
    ).toBeInTheDocument();
    expect(screen.getByText("me@acme.org (99)")).toBeInTheDocument();
    expect(screen.getByText("me-old@acme.org (gh-109284)")).toBeInTheDocument();
    expect(screen.getByText("alex@acme.org (u-2)")).toBeInTheDocument();

    await user.click(
      screen.getByTestId("super-admin-auth-provider-user-edit-101"),
    );
    const dialog = screen.getByTestId(
      "super-admin-auth-provider-user-edit-modal",
    );
    await user.click(within(dialog).getByTestId("dropdown-trigger"));

    expect(
      await screen.findByRole("option", { name: "alex@acme.org (u-2)" }),
    ).toBeInTheDocument();
  });
});
