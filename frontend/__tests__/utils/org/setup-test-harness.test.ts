import { beforeEach, describe, expect, it } from "vitest";
import {
  SETUP_TEST_PERSONA_STORAGE_KEY,
  getSetupTestSuperAdminAccessOverride,
  readSetupTestPersona,
  resolveTestSetupPersona,
  setSetupTestPersona,
} from "#/utils/org/setup-test-harness";

describe("setup-test-harness", () => {
  beforeEach(() => {
    window.localStorage.removeItem(SETUP_TEST_PERSONA_STORAGE_KEY);
    setSetupTestPersona("live");
  });

  it("defaults to live and leaves the resolved persona unchanged", () => {
    expect(readSetupTestPersona()).toBe("live");
    expect(resolveTestSetupPersona("owner")).toBe("owner");
    expect(getSetupTestSuperAdminAccessOverride()).toBeNull();
  });

  it("overrides persona and SA access when a test persona is set", () => {
    setSetupTestPersona("member");
    expect(readSetupTestPersona()).toBe("member");
    expect(resolveTestSetupPersona("super_admin")).toBe("member");
    expect(getSetupTestSuperAdminAccessOverride()).toBe(false);

    setSetupTestPersona("super_admin");
    expect(resolveTestSetupPersona("member")).toBe("super_admin");
    expect(getSetupTestSuperAdminAccessOverride()).toBe(true);
  });
});
