import { deferred } from "../helpers/native-fixtures";
import axios, { AxiosError, InternalAxiosRequestConfig, AxiosResponse } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { configureAuthentication, installAuthentication, getAuthentication } from "#/api/auth-adapter";

const response = (config: InternalAxiosRequestConfig<unknown>, data: unknown, status: number = 200): AxiosResponse<unknown, unknown> => ({ config, data, status, statusText: "", headers: {} });

describe("native browser CSRF", () => {
  beforeEach(() => { configureAuthentication("native"); getAuthentication().reset(); });

  it("deduplicates anonymous proof and rotates after login, password change and logout", async () => {
    let issued = 0;
    const received: string[] = [];
    const client = axios.create({ baseURL: "https://app.test", adapter: async (request) => {
      if (request.url === "/api/auth/csrf") return response(request, { csrf_token: `proof-${++issued}` });
      received.push(String(request.headers.get("X-CSRF-Token")));
      expect(request.withCredentials).toBe(true);
      return response(request, {});
    } });
    installAuthentication(client);
    await Promise.all([client.post("/api/authenticate"), client.post("/api/authenticate")]);
    expect(issued).toBe(1);
    await client.post("/api/auth/password/login", { email: "test@example.com", password: "long private password" });
    await client.post("/api/v1/settings", {});
    await client.post("/oauth/device/verify-authenticated", new URLSearchParams({ user_code: "ABCD-EFGH" }));
    await client.post("/api/auth/password/change", {});
    await client.post("/api/logout");
    await client.post("/api/auth/password/login", {});
    expect(received).toEqual(["proof-1", "proof-1", "proof-1", "proof-2", "proof-2", "proof-2", "proof-3", "proof-4"]);
  });

  it("refreshes expired-cookie proof once without retrying arbitrary forbidden mutations", async () => {
    let issued = 0;
    let mutations = 0;
    const client = axios.create({ baseURL: "https://app.test", adapter: async (request) => {
      if (request.url === "/api/auth/csrf") return response(request, { csrf_token: `proof-${++issued}` });
      mutations++;
      throw new AxiosError("Forbidden", "403", request, null, response(request, { detail: "Invalid CSRF token" }, 403));
    } });
    installAuthentication(client);
    await expect(client.post("/api/authenticate")).rejects.toThrow("Forbidden");
    expect(issued).toBe(2);
    expect(mutations).toBe(2);
    client.defaults.adapter = async (request) => {
      mutations++;
      throw new AxiosError("Forbidden", "403", request, null, response(request, { detail: "Permission denied" }, 403));
    };
    await expect(client.post("/api/admin/users/a/disable")).rejects.toThrow();
    expect(mutations).toBe(3);
  });

  it("never attaches proof to third-party requests or legacy-mode requests", async () => {
    const adapter = vi.fn(async (request: InternalAxiosRequestConfig<unknown>) => response(request, {}));
    const client = axios.create({ baseURL: "https://app.test", adapter });
    installAuthentication(client);
    await client.post("https://external.test/api/mutate");
    configureAuthentication("keycloak");
    await client.post("/api/authenticate");
    configureAuthentication(undefined);
    await client.post("/api/logout");
    expect(adapter).toHaveBeenCalledTimes(3);
    for (const [request] of adapter.mock.calls) expect(request.headers.get("X-CSRF-Token")).toBeUndefined();
  });

  it("discards an in-flight proof belonging to the pre-rotation cookie", async () => {
    const pending = deferred<void>();
    const release = (): void => pending.resolve(undefined);
    let waiting = false;
    let issued = 0;
    const client = axios.create({ baseURL: "https://app.test", adapter: async (request) => {
      if (request.url === "/api/auth/csrf") {
        issued++;
        if (issued === 1) { waiting = true; await pending.promise; }
        return response(request, { csrf_token: issued === 1 ? "old" : "new" });
      }
      expect(request.headers.get("X-CSRF-Token")).toBe("new");
      return response(request, {});
    } });
    installAuthentication(client);
    const mutation = client.post("/api/v1/settings");
    await vi.waitFor(() => expect(waiting).toBe(true));
    getAuthentication().reset(); release();
    await mutation;
    expect(issued).toBe(2);
  });
});
