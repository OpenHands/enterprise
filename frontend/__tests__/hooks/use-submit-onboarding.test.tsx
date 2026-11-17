import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { openHands } from "#/api/open-hands-axios";
import { useSubmitOnboarding } from "#/hooks/mutation/use-submit-onboarding";

const navigate = vi.fn();
vi.mock("react-router", () => ({ useNavigate: () => navigate }));
vi.mock("#/api/open-hands-axios", () => ({
  openHands: { post: vi.fn() },
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("resumes MCP authorization with a document load after onboarding", async () => {
  const replace = vi.fn();
  vi.stubGlobal("location", { origin: "https://example.com", replace });
  vi.mocked(openHands.post).mockResolvedValue({
    data: { message: "Onboarding completed" },
  });
  const client = new QueryClient();
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  const { result } = renderHook(() => useSubmitOnboarding(), {
    wrapper: Wrapper,
  });
  const returnTo = "/external-mcp/consent?request=opaque_handle";

  act(() => result.current.mutate({ selections: {}, returnTo }));

  await waitFor(() => expect(replace).toHaveBeenCalledWith(returnTo));
  expect(navigate).not.toHaveBeenCalled();
});
