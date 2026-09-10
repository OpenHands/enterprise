import type { AxiosInstance } from "axios";

let enterpriseBrowser = false;
let pendingSeed: Promise<string> | undefined;

export function configureBrowserCsrf(enabled: boolean) {
  enterpriseBrowser = enabled;
}

export function browserCsrfEnabled() {
  return enterpriseBrowser;
}

function cookieSeed(): string | undefined {
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("oh_csrf="));
  return cookie
    ? decodeURIComponent(cookie.slice("oh_csrf=".length))
    : undefined;
}

export async function ensureCsrfSeed(client: AxiosInstance): Promise<string> {
  const existing = cookieSeed();
  if (existing) return existing;
  if (!pendingSeed) {
    pendingSeed = client
      .get<{ csrf_token: string }>("/api/auth/csrf")
      .then(({ data }) => data.csrf_token)
      .finally(() => {
        pendingSeed = undefined;
      });
  }
  return pendingSeed;
}
