import { Provider } from "#/types/settings";

/** Navigate through the authenticated backend broker linking endpoint. */
export const generateIdpLinkUrl = (provider: Provider, requestUrl: URL) => {
  const url = new URL(
    `/api/auth/providers/${encodeURIComponent(provider)}/link`,
    requestUrl.origin,
  );
  url.searchParams.set(
    "redirect_url",
    `${requestUrl.pathname}${requestUrl.search}`,
  );
  return url.toString();
};
