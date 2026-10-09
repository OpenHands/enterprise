import React from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEmailVerification } from "#/hooks/use-email-verification";

const renderAt = (url: string) =>
  renderHook(() => useEmailVerification(), {
    wrapper: ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[url]}>{children}</MemoryRouter>
      </QueryClientProvider>
    ),
  });

describe("useEmailVerification", () => {
  it("flags a sign-in refused because the account is suspended", async () => {
    // Arrange & Act
    const { result } = renderAt("/login?account_disabled=true");

    // Assert
    await waitFor(() => expect(result.current.accountDisabled).toBe(true));
  });
});
