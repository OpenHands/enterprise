import { afterEach, describe, expect, it, vi } from "vitest";
import { AxiosError, AxiosHeaders } from "axios";
import { queryClient } from "#/query-client-config";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { LOGOUT_MUTATION_KEY } from "#/hooks/query/query-keys";

vi.mock("#/utils/custom-toast-handlers");

const make401 = () =>
  new AxiosError("Unauthorized", "401", undefined, undefined, {
    status: 401,
    statusText: "Unauthorized",
    headers: {},
    config: { headers: new AxiosHeaders() },
    data: { error: "NoCredentialsError" },
  });

describe("errors during logout", () => {
  afterEach(() => {
    queryClient.clear();
    vi.restoreAllMocks();
    vi.clearAllMocks();
  });

  it("ignores a polling 401 during logout, then restores normal error handling", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    let finishLogout!: () => void;
    const logout = queryClient
      .getMutationCache()
      .build(queryClient, {
        mutationKey: LOGOUT_MUTATION_KEY,
        mutationFn: () =>
          new Promise<void>((resolve) => {
            finishLogout = resolve;
          }),
      })
      .execute(undefined);
    await vi.waitFor(() => expect(finishLogout).toBeDefined());
    const poll = () =>
      queryClient.fetchQuery({
        queryKey: ["polling-during-logout"],
        queryFn: () => Promise.reject(make401()),
        retry: false,
      });

    await expect(poll()).rejects.toBeInstanceOf(AxiosError);
    expect(displayErrorToast).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();

    finishLogout();
    await logout;
    await expect(poll()).rejects.toBeInstanceOf(AxiosError);
    expect(displayErrorToast).toHaveBeenCalledWith("NoCredentialsError");
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["user", "authenticated"],
    });
  });

  it("still reports a failed logout request", async () => {
    const error = make401();
    await expect(
      queryClient
        .getMutationCache()
        .build(queryClient, {
          mutationKey: LOGOUT_MUTATION_KEY,
          mutationFn: () => Promise.reject(error),
          retry: false,
        })
        .execute(undefined),
    ).rejects.toBe(error);
    expect(displayErrorToast).toHaveBeenCalledWith("NoCredentialsError");
    expect(queryClient.isMutating({ mutationKey: LOGOUT_MUTATION_KEY })).toBe(
      0,
    );
  });
});

const make429 = () =>
  new AxiosError("Too Many Requests", "429", undefined, undefined, {
    status: 429,
    statusText: "Too Many Requests",
    headers: { "retry-after": "0" },
    config: { headers: new AxiosHeaders() },
    data: { detail: "rate_limited" },
  });

const make403 = (detail: string) =>
  new AxiosError(
    "Request failed with status code 403",
    "ERR_BAD_REQUEST",
    undefined,
    undefined,
    {
      status: 403,
      statusText: "Forbidden",
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: { detail },
    },
  );

describe("queryClient mutation defaults", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    queryClient.clear();
  });

  it("backs off before retrying a rate-limited mutation", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);

    const mutationFn = vi
      .fn()
      .mockRejectedValueOnce(make429())
      .mockResolvedValueOnce("ok");

    const result = queryClient
      .getMutationCache()
      .build(queryClient, { mutationFn, meta: { disableToast: true } })
      .execute(undefined);

    await vi.advanceTimersByTimeAsync(0);
    expect(mutationFn).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(999);
    expect(mutationFn).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toBe("ok");
    expect(mutationFn).toHaveBeenCalledTimes(2);
  });

  it("should not retry a mutation rejected with a non-429 error", async () => {
    const error = new AxiosError("Server Error", "500", undefined, undefined, {
      status: 500,
      statusText: "",
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: {},
    });
    const mutationFn = vi.fn().mockRejectedValue(error);

    await expect(
      queryClient
        .getMutationCache()
        .build(queryClient, { mutationFn, meta: { disableToast: true } })
        .execute(undefined),
    ).rejects.toBe(error);

    expect(mutationFn).toHaveBeenCalledTimes(1);
  });
});

describe("queryClient query error toasts", () => {
  afterEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
  });

  it.each([
    "Git provider token required (such as GitHub).",
    "Git provider token required.",
    "Git provider not connected",
  ])("does not toast the expected no-git-provider 403: %s", async (detail) => {
    const error = make403(detail);

    await expect(
      queryClient.fetchQuery({
        queryKey: ["no-git-provider", detail],
        queryFn: () => Promise.reject(error),
        retry: false,
      }),
    ).rejects.toBe(error);

    expect(displayErrorToast).not.toHaveBeenCalled();
  });

  it("still toasts a 403 with an unrelated detail message", async () => {
    const error = make403("You do not have permission to access this resource");

    await expect(
      queryClient.fetchQuery({
        queryKey: ["unrelated-403"],
        queryFn: () => Promise.reject(error),
        retry: false,
      }),
    ).rejects.toBe(error);

    expect(displayErrorToast).toHaveBeenCalledWith(
      "You do not have permission to access this resource",
    );
  });
});
