/** Browser cookie that puts mock SaaS into an empty first-install Super Admin state. */
export const MOCK_FRESH_SA_COOKIE = "oh_mock_fresh_sa=1";

export function requestWantsFreshSa(request: Request): boolean {
  return (request.headers.get("cookie") ?? "").includes(MOCK_FRESH_SA_COOKIE);
}
