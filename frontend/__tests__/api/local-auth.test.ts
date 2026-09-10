import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "#/mocks/node";
import AuthService from "#/api/auth-service/auth-service.api";
import { openHands } from "#/api/open-hands-axios";
import { configureBrowserCsrf } from "#/api/auth-service/browser-csrf";
import { safeAuthRedirect } from "#/utils/auth-redirect";

beforeEach(() => {
  configureBrowserCsrf(false);
  document.cookie = "oh_csrf=; Max-Age=0; Path=/";
});
afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "oh_csrf=; Max-Age=0; Path=/";
});

describe("local authentication HTTP transport", () => {
  it("seeds CSRF before login, sends the header, and preserves password bytes and context", async () => {
    const sequence: string[] = [];
    let submitted: unknown;
    let csrf: string | null = null;
    server.use(
      http.get("/api/auth/csrf", () => {
        sequence.push("csrf");
        return HttpResponse.json({ csrf_token: "csrf-proof" });
      }),
      http.post("/api/auth/login", async ({ request }) => {
        sequence.push("login");
        csrf = request.headers.get("X-CSRF-Token");
        submitted = await request.json();
        return HttpResponse.json({
          password_change_required: true,
          redirect_url: "/auth/change-password",
        });
      }),
    );
    const body = {
      email: "admin@example.com",
      password: " space and 🔑 password ",
      redirect_url: "/oauth/device/verify?user_code=ABCD-EFGH",
      invitation_token: "invite",
    };
    expect(await AuthService.login(body)).toEqual({
      password_change_required: true,
      redirect_url: "/auth/change-password",
    });
    expect(sequence).toEqual(["csrf", "login"]);
    expect(submitted).toEqual(body);
    expect(csrf).toBe("csrf-proof");
  });

  it.each(["authenticate", "logout"] as const)(
    "protects the existing Enterprise %s endpoint with CSRF",
    async (action) => {
      let csrf: string | null = null;
      server.use(
        http.get("/api/auth/csrf", () =>
          HttpResponse.json({ csrf_token: "csrf-proof" }),
        ),
        http.post(`/api/${action}`, ({ request }) => {
          csrf = request.headers.get("X-CSRF-Token");
          return HttpResponse.json({});
        }),
      );
      await AuthService[action]("saas");
      expect(csrf).toBe("csrf-proof");
    },
  );

  it("forwards the CSRF cookie on other existing browser mutations", async () => {
    document.cookie = "oh_csrf=csrf-cookie; Path=/";
    let csrf: string | null = null;
    server.use(
      http.post("/api/email", ({ request }) => {
        csrf = request.headers.get("X-CSRF-Token");
        return HttpResponse.json({});
      }),
    );
    await openHands.post("/api/email", { email: "new@example.com" });
    expect(csrf).toBe("csrf-cookie");
  });

  it("reseeds once for concurrent Enterprise writes after session rotation", async () => {
    configureBrowserCsrf(true);
    let seedRequests = 0;
    const received: (string | null)[] = [];
    server.use(
      http.get("/api/auth/csrf", async () => {
        seedRequests += 1;
        await new Promise((resolve) => {
          setTimeout(resolve, 10);
        });
        return HttpResponse.json({ csrf_token: "rotated-proof" });
      }),
      http.post("/api/settings", ({ request }) => {
        received.push(request.headers.get("X-CSRF-Token"));
        return HttpResponse.json({});
      }),
    );
    await Promise.all([
      openHands.post("/api/settings", {}),
      openHands.post("/api/settings", {}),
    ]);
    expect(seedRequests).toBe(1);
    expect(received).toEqual(["rotated-proof", "rotated-proof"]);
  });

  it("preserves OSS writes without requesting Enterprise CSRF endpoints", async () => {
    let seedRequests = 0;
    server.use(
      http.get("/api/auth/csrf", () => {
        seedRequests += 1;
        return new HttpResponse(null, { status: 404 });
      }),
      http.post("/api/settings", () => HttpResponse.json({})),
    );
    await openHands.post("/api/settings", {});
    expect(seedRequests).toBe(0);
  });

  it("redirects a restricted session to the server-owned password change path", async () => {
    vi.stubGlobal("location", {
      href: "http://localhost:3000/settings/user",
      pathname: "/settings/user",
      search: "",
    });
    server.use(
      http.get("/api/protected", () =>
        HttpResponse.json(
          {
            detail: {
              code: "password_change_required",
              redirect_url: "/auth/change-password?returnTo=%2Fsettings%2Fuser",
            },
          },
          { status: 403 },
        ),
      ),
    );
    await expect(openHands.get("/api/protected")).rejects.toThrow();
    expect(window.location.href).toBe(
      "/auth/change-password?returnTo=%2Fsettings%2Fuser",
    );
  });

  it.each([403, 409, 503])(
    "keeps ordinary authorization/provider failures (%s) separate from login",
    async (status) => {
      const href = "http://localhost:3000/settings/integrations";
      vi.stubGlobal("location", {
        href,
        pathname: "/settings/integrations",
        search: "",
      });
      server.use(
        http.get("/api/protected", () =>
          HttpResponse.json(
            { detail: { code: "provider_reconnect_required" } },
            { status },
          ),
        ),
      );
      await expect(openHands.get("/api/protected")).rejects.toThrow();
      expect(window.location.href).toBe(href);
    },
  );
});

it.each([
  "https://evil.example",
  "//evil.example",
  "/\\evil.example",
  "/\nevil.example",
  "javascript:alert(1)",
])("rejects unsafe authentication destinations: %s", (destination) => {
  expect(safeAuthRedirect(destination)).toBe("/");
});
