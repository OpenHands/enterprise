import { QueryCache, MutationCache, QueryClient } from "@tanstack/react-query";
import i18next from "i18next";
import { AxiosError } from "axios";
import { I18nKey } from "./i18n/declaration";
import { retrieveAxiosErrorMessage } from "./utils/retrieve-axios-error-message";
import { isAxiosErrorWithDetailField } from "./utils/type-guards";
import { displayErrorToast } from "./utils/custom-toast-handlers";
import {
  isRateLimitError,
  getRateLimitRetryDelayMs,
} from "./utils/rate-limit-retry";

const handle401Error = (error: AxiosError, queryClient: QueryClient) => {
  if (error?.response?.status === 401 || error?.status === 401) {
    queryClient.invalidateQueries({ queryKey: ["user", "authenticated"] });
  }
};

// Authenticated users without a connected git provider (e.g. SSO/SAML-only
// enterprise users) receive 403s with these messages from git endpoints. That
// is a valid state rather than an actionable error, so it must never surface
// as a toast. Producers: openhands/app_server/git/git_router.py,
// openhands/app_server/user/user_router.py,
// enterprise/server/routes/users_v1.py.
const isExpectedNoGitProviderError = (error: AxiosError): boolean =>
  error.response?.status === 403 &&
  isAxiosErrorWithDetailField(error) &&
  /^git provider (token required|not connected)/i.test(
    error.response?.data.detail ?? "",
  );

const shownErrors = new Set<string>();
export const queryClient = new QueryClient({
  defaultOptions: {
    mutations: {
      // A 429 means the request was rejected before being processed, so a
      // retry can't double-apply the mutation. Bursts (e.g. the post-login
      // query storm) can momentarily trip the per-user API rate limit and
      // would otherwise surface as a spurious error toast.
      retry: (failureCount, error) =>
        failureCount < 2 && isRateLimitError(error),
      retryDelay: (failureCount, error) =>
        getRateLimitRetryDelayMs(failureCount, error),
    },
  },
  queryCache: new QueryCache({
    onError: (error, query) => {
      const isAuthQuery =
        query.queryKey[0] === "user" && query.queryKey[1] === "authenticated";
      if (!isAuthQuery) {
        handle401Error(error, queryClient);
      }

      if (!query.meta?.disableToast && !isExpectedNoGitProviderError(error)) {
        const errorMessage = retrieveAxiosErrorMessage(error);

        if (!shownErrors.has(errorMessage || "")) {
          displayErrorToast(errorMessage || i18next.t(I18nKey.ERROR$GENERIC));
          shownErrors.add(errorMessage);

          setTimeout(() => {
            shownErrors.delete(errorMessage);
          }, 3000);
        }
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, _, __, mutation) => {
      handle401Error(error, queryClient);

      if (!mutation?.meta?.disableToast) {
        const message = retrieveAxiosErrorMessage(error);
        displayErrorToast(message || i18next.t(I18nKey.ERROR$GENERIC));
      }
    },
  }),
});
