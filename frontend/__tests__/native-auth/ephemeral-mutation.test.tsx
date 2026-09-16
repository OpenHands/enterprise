import { deferred } from "../helpers/native-fixtures";
import { act, renderHook, waitFor } from "@testing-library/react";
import { onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";
import { describe, expect, it, vi } from "vitest";
import { useEphemeralMutation } from "#/hooks/mutation/use-ephemeral-mutation";

describe("secret-bearing mutations", () => {
  it("does not queue sensitive operations while offline or replay them after reconnect", async () => {
    const client = new QueryClient();
    const operation = vi.fn(async (_: string) => { throw new Error("Offline"); });
    const { result, unmount } = renderHook(() => useEphemeralMutation(operation), { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
    onlineManager.setOnline(false);
    try {
      await act(async () => { await expect(result.current.run("offline-secret")).rejects.toThrow(); });
      expect(operation).toHaveBeenCalledOnce();
      const states = client.getMutationCache().getAll().map((mutation) => mutation.state);
      expect(states.every((state) => !state.isPaused && state.variables === undefined)).toBe(true);
      expect(JSON.stringify(states)).not.toContain("offline-secret");
      unmount();
      onlineManager.setOnline(true);
      await act(async () => {});
      expect(operation).toHaveBeenCalledOnce();
    } finally { onlineManager.setOnline(true); }
  });
  it("stores neither input passwords nor returned one-time links in MutationCache", async () => {
    const client = new QueryClient();
    const { promise, resolve: release } = deferred<{ invite_url: string }>();
    const operation = vi.fn((_input: { password: string }): Promise<{ invite_url: string }> => promise);
    const { result } = renderHook(() => useEphemeralMutation(operation), { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
    const { promise: completion, resolve: complete } = deferred<{ invite_url: string }>();
    await act(async () => { result.current.run({ password: "secret-password-value" }).then(complete); });
    expect(result.current.isPending).toBe(true);
    const cached = (): string => JSON.stringify(client.getMutationCache().getAll().map((mutation) => mutation.state));
    expect(cached()).not.toContain("secret-password-value");
    expect(client.getMutationCache().getAll()[0].state.variables).toBeUndefined();
    await act(async () => { release({ invite_url: "https://app.test/account-setup#token=private-token" }); await completion; });
    await waitFor(() => expect(result.current.isPending).toBe(false));
    expect(await completion).toEqual({ invite_url: "https://app.test/account-setup#token=private-token" });
    expect(cached()).not.toContain("private-token");
    expect(client.getMutationCache().getAll().every((mutation) => mutation.state.data === undefined)).toBe(true);
  });

  it("drops Axios request config when a secret-bearing request fails", async () => {
    const client = new QueryClient();
    const config = { headers: new AxiosHeaders(), data: { token: "private-input" } };
    const operation = vi.fn(async (_: string) => { throw new AxiosError("Failure", "400", config, null, { config, data: { detail: "Link expired" }, status: 400, statusText: "", headers: {} }); });
    const { result } = renderHook(() => useEphemeralMutation(operation), { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
    await act(async () => { await expect(result.current.run("private-input")).rejects.toThrow("Link expired"); });
    const states = client.getMutationCache().getAll().map((mutation) => mutation.state);
    expect(JSON.stringify(states)).not.toContain("private-input");
    expect(states.every((state) => !(state.error && "config" in state.error))).toBe(true);
    expect(operation).toHaveBeenCalledTimes(1);
  });
});
