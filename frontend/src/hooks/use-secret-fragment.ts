import { useLayoutEffect, useState } from "react";

/** Read before effects, scrub before any analytics effect, retain only in this component. */
export function useSecretFragment(): readonly [string, () => void] {
  const [token, setToken] = useState(
    () => new URLSearchParams(window.location.hash.slice(1)).get("token") || "",
  );
  useLayoutEffect(() => {
    window.history.replaceState(
      window.history.state,
      "",
      window.location.pathname + window.location.search,
    );
  }, []);
  return [token, () => setToken("")] as const;
}
