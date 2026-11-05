import { describe, expect, it } from "vitest";
import { getAcpProviderSecrets } from "#/constants/acp-provider-secrets";
import { I18nKey } from "#/i18n/declaration";
import translations from "#/i18n/translation.json";

describe("Codex ACP provider secrets", () => {
  it("explains which auth.json shape is accepted", () => {
    const codexAuth = getAcpProviderSecrets("codex")[0]!;
    const hint = translations.SETTINGS$ACP_SECRET_CODEX_AUTH_HINT.en;

    expect(codexAuth.hint_key).toBe(
      I18nKey.SETTINGS$ACP_SECRET_CODEX_AUTH_HINT,
    );
    expect(hint).toContain("auth_mode is chatgpt");
    expect(hint).toContain("tokens.refresh_token is set");
    expect(hint).toContain("use OPENAI_API_KEY instead");
  });
});
