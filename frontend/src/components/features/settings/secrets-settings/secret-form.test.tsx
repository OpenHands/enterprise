import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SecretForm } from "./secret-form";

// The org-scoped-secrets regression (OHE-3342) let the edit form route an
// org-shared secret's update to the personal V1 endpoint when the "share with
// organization" checkbox was toggled off. These tests pin the fix: edits route
// by the secret's ORIGINAL scope, and the share checkbox only appears on add.

const updateSecretMock = vi.hoisted(() => vi.fn());
const createSecretMock = vi.hoisted(() => vi.fn());
const updateOrgSecretMock = vi.hoisted(() => vi.fn());
const createOrgSecretMock = vi.hoisted(() => vi.fn());

vi.mock("#/hooks/mutation/use-update-secret", () => ({
  useUpdateSecret: () => ({ mutate: updateSecretMock }),
}));

vi.mock("#/hooks/mutation/use-create-secret", () => ({
  useCreateSecret: () => ({ mutate: createSecretMock }),
}));

vi.mock("#/api/secrets-service", () => ({
  SecretsService: {
    updateSecret: vi.fn(async (...args: unknown[]) => {
      updateSecretMock(...(args as [string, string, string | undefined]));
    }),
    createSecret: vi.fn(async (...args: unknown[]) => {
      createSecretMock(...(args as [string, string, string | undefined]));
    }),
    searchSecrets: vi.fn(),
    getSecrets: vi.fn(),
  },
}));

vi.mock("#/api/organization-service/organization-service.api", () => ({
  organizationService: {
    updateOrgSecret: vi.fn(async (...args: unknown[]) => {
      updateOrgSecretMock(
        ...(args as [string, string, { name: string; description?: string }]),
      );
    }),
    createOrgSecret: vi.fn(async (...args: unknown[]) => {
      createOrgSecretMock(
        ...(args as [
          string,
          { name: string; value: string; description?: string },
        ]),
      );
    }),
    deleteOrgSecret: vi.fn(),
  },
}));

vi.mock("#/hooks/query/use-get-secrets", () => ({
  useSearchSecrets: () => ({
    data: [
      { name: "ORG_TOKEN", description: "shared", scope: "organization" },
      { name: "PERSONAL_KEY", description: "mine", scope: "personal" },
    ],
    isLoading: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    onLoadMore: vi.fn(),
  }),
}));

vi.mock("#/context/use-selected-organization", () => ({
  useSelectedOrganizationId: () => ({ organizationId: "org-123" }),
}));

vi.mock("#/hooks/query/use-me", () => ({
  useMe: () => ({
    data: { id: "user-1", role: "admin" },
  }),
}));

vi.mock("#/hooks/organizations/use-permissions", () => ({
  usePermission: () => ({ hasPermission: () => true }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("../settings-input", () => ({
  SettingsInput: ({
    name,
    defaultValue,
    testId,
  }: {
    name: string;
    defaultValue?: string;
    testId?: string;
  }) => (
    <input
      data-testid={testId}
      name={name}
      defaultValue={defaultValue}
      type="text"
    />
  ),
}));

vi.mock("../brand-button", () => ({
  BrandButton: ({
    children,
    onClick,
    type = "button",
    testId,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    type?: "button" | "submit";
    testId?: string;
  }) => (
    // eslint-disable-next-line react/button-has-type
    <button data-testid={testId} type={type} onClick={onClick}>
      {children}
    </button>
  ),
}));

vi.mock("../optional-tag", () => ({
  OptionalTag: () => <span />,
}));

function withClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("SecretForm edit-mode scope routing (OHE-3342)", () => {
  it("routes an org-shared secret edit to the org-secrets endpoint", async () => {
    withClient(
      <SecretForm mode="edit" selectedSecret="ORG_TOKEN" onCancel={vi.fn()} />,
    );

    fireEvent.change(screen.getByTestId("name-input"), {
      target: { value: "ORG_TOKEN_RENAMED" },
    });
    fireEvent.change(screen.getByTestId("description-input"), {
      target: { value: "updated desc" },
    });
    fireEvent.click(screen.getByTestId("submit-button"));

    await waitFor(() => expect(updateSecretMock).toHaveBeenCalledOnce());
    const [args] = updateSecretMock.mock.calls[0];
    // Routed by the ORIGINAL scope (organization), not the (now hidden) checkbox.
    expect(args).toMatchObject({
      secretToEdit: "ORG_TOKEN",
      name: "ORG_TOKEN_RENAMED",
      description: "updated desc",
      isShared: true,
      organizationId: "org-123",
    });
  });

  it("routes a personal secret edit to the personal V1 endpoint", async () => {
    withClient(
      <SecretForm
        mode="edit"
        selectedSecret="PERSONAL_KEY"
        onCancel={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByTestId("name-input"), {
      target: { value: "PERSONAL_KEY" },
    });
    fireEvent.change(screen.getByTestId("description-input"), {
      target: { value: "mine updated" },
    });
    fireEvent.click(screen.getByTestId("submit-button"));

    await waitFor(() => expect(updateSecretMock).toHaveBeenCalledOnce());
    const [args] = updateSecretMock.mock.calls[0];
    expect(args).toMatchObject({
      secretToEdit: "PERSONAL_KEY",
      name: "PERSONAL_KEY",
      description: "mine updated",
      isShared: false,
      organizationId: "org-123",
    });
  });

  it("does not show the share-with-org checkbox in edit mode", () => {
    withClient(
      <SecretForm mode="edit" selectedSecret="ORG_TOKEN" onCancel={vi.fn()} />,
    );
    expect(screen.queryByTestId("share-with-org-checkbox")).toBeNull();
  });

  it("shows the share-with-org checkbox in add mode", () => {
    withClient(
      <SecretForm mode="add" selectedSecret={null} onCancel={vi.fn()} />,
    );
    expect(screen.getByTestId("share-with-org-checkbox")).toBeInTheDocument();
  });
});
