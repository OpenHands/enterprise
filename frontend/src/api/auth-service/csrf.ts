import { openHands } from "../open-hands-axios";
import { configureBrowserCsrf, ensureCsrfSeed } from "./browser-csrf";

export async function authPost<T>(path: string, body: unknown = {}) {
  configureBrowserCsrf(true);
  const token = await ensureCsrfSeed(openHands);
  return openHands.post<T>(path, body, {
    headers: { "X-CSRF-Token": token },
  });
}
