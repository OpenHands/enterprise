import { beforeEach, describe, expect, it } from "vitest";
import {
  SUPER_ADMIN_NUX_STORAGE_KEY,
  getSuperAdminNuxPath,
  getSuperAdminNuxStep,
  markSuperAdminNuxAccountDone,
  markSuperAdminNuxTosDone,
  markSuperAdminNuxWelcomeDone,
  readSuperAdminNux,
  resetSuperAdminNux,
  setSuperAdminNuxStep,
} from "#/utils/org/super-admin-nux";

describe("super-admin-nux", () => {
  beforeEach(() => {
    window.localStorage.removeItem(SUPER_ADMIN_NUX_STORAGE_KEY);
    resetSuperAdminNux();
  });

  it("starts at welcome and advances through TOS and account", () => {
    expect(getSuperAdminNuxStep()).toBe("welcome");
    expect(getSuperAdminNuxPath()).toBe("/install");

    markSuperAdminNuxWelcomeDone();
    expect(getSuperAdminNuxStep()).toBe("tos");
    expect(getSuperAdminNuxPath()).toBe("/install/tos");

    markSuperAdminNuxTosDone();
    expect(getSuperAdminNuxStep()).toBe("account");
    expect(getSuperAdminNuxPath()).toBe("/install/account");

    markSuperAdminNuxAccountDone({ name: "Ada", email: "ada@example.com" });
    expect(getSuperAdminNuxStep()).toBe("done");
    expect(getSuperAdminNuxPath()).toBe("/super-admin/setup");
    expect(readSuperAdminNux().account?.email).toBe("ada@example.com");
  });

  it("can jump to a specific NUX step for testing", () => {
    setSuperAdminNuxStep("tos");
    expect(getSuperAdminNuxStep()).toBe("tos");
    expect(getSuperAdminNuxPath()).toBe("/install/tos");

    setSuperAdminNuxStep("account");
    expect(getSuperAdminNuxStep()).toBe("account");

    setSuperAdminNuxStep("done");
    expect(getSuperAdminNuxStep()).toBe("done");
    expect(readSuperAdminNux().account?.email).toBe("me@acme.org");

    setSuperAdminNuxStep("welcome");
    expect(getSuperAdminNuxStep()).toBe("welcome");
  });
});
