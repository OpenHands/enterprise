import { execFileSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  copyFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

function stringRecord(value: unknown): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("Expected a translation object");
  return Object.fromEntries(Object.entries(value).map(([key, text]: [string, unknown]): [string, string] => {
    if (typeof text !== "string") throw new Error(`Invalid translation: ${key}`);
    return [key, text];
  }));
}

describe("native authentication translation distribution", () => {
  const root = path.resolve(__dirname, "../..");
  const source: unknown = JSON.parse(
    readFileSync(path.join(root, "src/i18n/translation.json"), "utf8"),
  );
  if (typeof source !== "object" || source === null) throw new Error("Expected translation source object");
  const nativeEntries = Object.entries(source).filter(([key]) =>
    /^(NATIVE_AUTH|NATIVE_GIT)\$/.test(key),
  ).map(([key, value]: [string, unknown]): [string, Record<string, string>] => [key, stringRecord(value)]);
  let generated: string;

  beforeAll(() => {
    generated = mkdtempSync(path.join(tmpdir(), "native-translations-"));
    mkdirSync(path.join(generated, "scripts"));
    mkdirSync(path.join(generated, "src/i18n"), { recursive: true });
    for (const file of [
      "scripts/make-i18n-translations.cjs",
      "src/i18n/translation.json",
    ]) {
      copyFileSync(path.join(root, file), path.join(generated, file));
    }
    execFileSync(process.execPath, [
      path.join(generated, "scripts/make-i18n-translations.cjs"),
    ]);
  });

  afterAll(() => {
    if (generated) rmSync(generated, { recursive: true, force: true });
  });

  it("ships every native label in generated English resources", () => {
    const english = stringRecord(JSON.parse(
      readFileSync(
        path.join(generated, "public/locales/en/translation.json"),
        "utf8",
      ),
    ));
    expect(nativeEntries.length).toBeGreaterThan(0);
    for (const [key, translations] of nativeEntries) {
      expect(translations, key).toEqual(
        expect.objectContaining({ en: expect.any(String) }),
      );
      expect(english[key], key).toBe(
        translations.en,
      );
    }
    expect(english["NATIVE_AUTH$SIGN_IN"]).toBe("Sign in");
    expect(english["NATIVE_AUTH$SIGN_IN_EMAIL"]).toBe("Sign in with Email");
    expect(english["NATIVE_AUTH$SIGN_IN_SSO"]).toBe("Sign in with SSO");
    expect(english["NATIVE_GIT$CONNECT"]).toBe("Connect");
  });

  it("does not distribute character indexes as language codes", () => {
    const locales = readdirSync(path.join(generated, "public/locales"));
    expect(locales.filter((locale) => /^\d+$/.test(locale))).toEqual([]);
  });
});
