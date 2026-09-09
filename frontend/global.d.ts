interface Window {
  __APP_MODE__?: "saas" | "oss";
  __GITHUB_CLIENT_ID__?: string | null;
  grecaptcha?: {
    enterprise: {
      ready: (callback: () => void) => void;
      execute: (
        siteKey: string,
        options: { action: string },
      ) => Promise<string>;
    };
  };
}
