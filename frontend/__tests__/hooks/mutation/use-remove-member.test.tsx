import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRemoveMember } from "#/hooks/mutation/use-remove-member";
import { useSelectedOrganizationStore } from "#/stores/selected-organization-store";
import { organizationService } from "#/api/organization-service/organization-service.api";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";

vi.mock("#/utils/custom-toast-handlers", () => ({
  displayErrorToast: vi.fn(),
  displaySuccessToast: vi.fn(),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// Mock the useRevalidator hook from react-router
vi.mock("react-router", () => ({
  useRevalidator: () => ({
    revalidate: vi.fn(),
  }),
}));

describe("useRemoveMember", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    useSelectedOrganizationStore.setState({ organizationId: null });
  });

  it.each([false, true])(
    "reports revocation_pending=%s truthfully and refreshes the member list",
    async (pending) => {
      useSelectedOrganizationStore.setState({ organizationId: "org-123" });
      const remove = vi
        .spyOn(organizationService, "removeMember")
        .mockResolvedValue({
          message: pending
            ? "Revocation pending"
            : "Member removed successfully",
          ...(pending ? { revocation_pending: true } : {}),
        });
      const queryClient = new QueryClient();
      const invalidate = vi.spyOn(queryClient, "invalidateQueries");
      const { result } = renderHook(() => useRemoveMember(), {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        ),
      });
      result.current.mutate({ userId: "user-123" });
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(remove).toHaveBeenCalledWith({
        orgId: "org-123",
        userId: "user-123",
      });
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["organizations", "members", "org-123"],
      });
      if (pending) {
        expect(displaySuccessToast).not.toHaveBeenCalled();
        expect(displayErrorToast).toHaveBeenCalledWith(
          "ORG$REMOVE_MEMBER_REVOCATION_PENDING",
        );
      } else {
        expect(displayErrorToast).not.toHaveBeenCalled();
        expect(displaySuccessToast).toHaveBeenCalledWith(
          "ORG$REMOVE_MEMBER_SUCCESS",
        );
      }
    },
  );

  it("should throw an error when organizationId is null", async () => {
    const queryClient = new QueryClient({
      defaultOptions: {
        mutations: {
          retry: false,
        },
      },
    });

    const { result } = renderHook(() => useRemoveMember(), {
      wrapper: ({ children }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    });

    // Attempt to mutate without organizationId
    result.current.mutate({ userId: "user-123" });

    // Should fail with an error about missing organizationId
    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });

    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe("Organization ID is required");
  });
});
