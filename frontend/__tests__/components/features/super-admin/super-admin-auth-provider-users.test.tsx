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
  enabled: true,
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
      user_name: "Alice",
      external_subject_id: "gh-alice",
      external_email: "alice@old-email.com",
    },
    {
      id: 11,
      user_id: "user-2",
      email: "bob@example.com",
      user_name: "Bob",
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
    memberships: [
      { org_id: "user-1", org_name: "user_user-1_org", role: "owner", status: "active" },
      { org_id: "org-acme", org_name: "Acme Corp", role: "admin", status: "active" },
    ],
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
    memberships: [
      { org_id: "org-globex", org_name: "Globex", role: "member", status: "active" },
    ],
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
// only resolves `{{title}}` with a real i18n instance. `useTranslation` is
// mocked in `vitest.setup.ts` to return the key itself, so the other labels
// on this page are asserted as keys.
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
    // The admin picks accounts by name + email; the raw ids stay visible as
    // the secondary line so a link can still be identified precisely.
    expect(screen.getByText("Alice <alice@example.com>")).toBeInTheDocument();
    expect(screen.getByText("user-1")).toBeInTheDocument();
    expect(screen.getByText("alice@old-email.com")).toBeInTheDocument();
    expect(screen.getByText("gh-alice")).toBeInTheDocument();
    expect(screen.getByText("Bob <bob@example.com>")).toBeInTheDocument();
    expect(screen.getByText("user-2")).toBeInTheDocument();
    expect(screen.getByText("gh-bob")).toBeInTheDocument();
    // No external email from the provider: say so instead of leaving the
    // secondary line empty.
    expect(
      screen.getByText(
        "SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS_NO_EXTERNAL_EMAIL",
      ),
    ).toBeInTheDocument();
  });

  it("re-points a link to a different, already-existing user", async () => {
    const user = userEvent.setup();
    const updateAuthProviderUser = vi
      .spyOn(authProvidersService, "updateAuthProviderUser")
      .mockResolvedValue({
        id: 10,
        user_id: "user-3",
        email: "carol@example.com",
        user_name: "Carol",
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
      screen.getByRole("option", { name: "Carol <carol@example.com>" }),
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
        user_name: "Alice",
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
      name: "Alice <alice@example.com>",
    });
    expect(aliceOption).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Bob <bob@example.com>" }),
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

  it("keeps the identity and its current target visible in the re-point dialog", async () => {
    // The dropdown lists every account on the instance, so the dialog has to
    // restate what is being moved and where it points today -- otherwise the
    // admin is picking from an unlabelled list.
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-user-edit-10"),
    );
    const dialog = screen.getByTestId(
      "super-admin-auth-provider-user-edit-modal",
    );

    expect(
      within(dialog).getByText(
        "SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_EXTERNAL_LABEL",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("alice@old-email.com")).toBeInTheDocument();
    expect(within(dialog).getByText("gh-alice")).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "SUPER_ADMIN$AUTH_PROVIDER_USER_LINK_CURRENT_LABEL",
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Alice <alice@example.com>"),
    ).toBeInTheDocument();
  });

  it("shows the selected account, its id and its organizations as the dropdown changes", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(
      await screen.findByTestId("super-admin-auth-provider-user-edit-10"),
    );
    const dialog = screen.getByTestId(
      "super-admin-auth-provider-user-edit-modal",
    );
    const target = within(dialog).getByTestId(
      "super-admin-auth-provider-user-edit-target",
    );

    // Before anything is picked: the account the link points at today. The
    // personal workspace shares the user's id, so it's labelled as such.
    expect(within(target).getByText("Alice <alice@example.com>")).toBeInTheDocument();
    expect(await within(target).findByText("Acme Corp")).toBeInTheDocument();
    expect(within(target).getByText("org-acme")).toBeInTheDocument();
    expect(within(target).getByText("ORG$PERSONAL_WORKSPACE")).toBeInTheDocument();
    // The user id, plus the personal workspace's org id (which is the same).
    expect(within(target).getAllByText("user-1")).toHaveLength(2);

    await user.type(within(dialog).getByRole("combobox"), "carol");
    await user.click(
      screen.getByRole("option", { name: "Carol <carol@example.com>" }),
    );

    expect(within(target).getByText("Carol <carol@example.com>")).toBeInTheDocument();
    expect(within(target).getByText("user-3")).toBeInTheDocument();
    expect(within(target).getByText("Globex")).toBeInTheDocument();
    expect(within(target).getByText("org-globex")).toBeInTheDocument();
    expect(within(target).queryByText("Alice <alice@example.com>")).not.toBeInTheDocument();
    expect(within(target).queryByText("Acme Corp")).not.toBeInTheDocument();

    const combobox = within(dialog).getByRole("combobox");
    await user.clear(combobox);
    await user.type(combobox, "bob");
    await user.click(screen.getByRole("option", { name: "Bob <bob@example.com>" }));

    expect(within(target).getByText("Bob <bob@example.com>")).toBeInTheDocument();
    expect(within(target).getByText("ORG$NO_ORGANIZATION")).toBeInTheDocument();
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
    expect(screen.getByText("openhands <me@acme.org>")).toBeInTheDocument();
    expect(screen.getByText("me-old@acme.org")).toBeInTheDocument();
    expect(screen.getByText("Alex Rivera <alex@acme.org>")).toBeInTheDocument();

    await user.click(
      screen.getByTestId("super-admin-auth-provider-user-edit-101"),
    );
    const dialog = screen.getByTestId(
      "super-admin-auth-provider-user-edit-modal",
    );
    await user.click(within(dialog).getByTestId("dropdown-trigger"));

    expect(
      await screen.findByRole("option", {
        name: "Alex Rivera <alex@acme.org>",
      }),
    ).toBeInTheDocument();
  });

  it("filters the links by the linked user's display name", async () => {
    // Searching by name is what an admin actually has in hand when
    // re-pointing a link, so it has to reach the server-side `q` filter.
    // "Rivera" appears only in the display name, never in an email or id.
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Identity links to accounts for GitHub.");
    await user.type(
      screen.getByTestId("super-admin-auth-provider-users-search"),
      "Rivera",
    );

    expect(
      await screen.findByText("Alex Rivera <alex@acme.org>"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByText("openhands <me@acme.org>"),
      ).not.toBeInTheDocument(),
    );
  });
});
