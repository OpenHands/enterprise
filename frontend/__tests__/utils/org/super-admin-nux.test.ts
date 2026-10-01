import { beforeEach, describe, expect, it } from "vitest";
import {
  SUPER_ADMIN_NUX_LLM_PATH,
  SUPER_ADMIN_NUX_STORAGE_KEY,
  clearSuperAdminNuxStarterModal,
  getSuperAdminNuxPath,
  getSuperAdminNuxStep,
  markSuperAdminNuxAccountDone,
  markSuperAdminNuxCompanyDone,
  markSuperAdminNuxOrgDone,
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

  it("starts at welcome and advances through TOS, account, company, and org", () => {
    expect(getSuperAdminNuxStep()).toBe("welcome");
    expect(getSuperAdminNuxPath()).toBe("/install");

    markSuperAdminNuxWelcomeDone();
    expect(getSuperAdminNuxStep()).toBe("tos");
    expect(getSuperAdminNuxPath()).toBe("/install/tos");

    markSuperAdminNuxTosDone();
    expect(getSuperAdminNuxStep()).toBe("account");
    expect(getSuperAdminNuxPath()).toBe("/install/account");

    markSuperAdminNuxAccountDone({ name: "Ada", email: "ada@example.com" });
    expect(getSuperAdminNuxStep()).toBe("company");
    expect(getSuperAdminNuxPath()).toBe("/install/company");
    expect(readSuperAdminNux().account?.email).toBe("ada@example.com");

    markSuperAdminNuxCompanyDone({ name: "Acme", hasLicenseKey: true });
    expect(getSuperAdminNuxStep()).toBe("org");
    expect(getSuperAdminNuxPath()).toBe("/install/org");
    expect(readSuperAdminNux().company).toEqual({
      name: "Acme",
      hasLicenseKey: true,
      hasLogo: false,
    });

    markSuperAdminNuxOrgDone({ name: "My Organization" });
    expect(getSuperAdminNuxStep()).toBe("done");
    expect(getSuperAdminNuxPath()).toBe(SUPER_ADMIN_NUX_LLM_PATH);
    expect(readSuperAdminNux().starterModalPending).toBe(true);

    clearSuperAdminNuxStarterModal();
    expect(readSuperAdminNux().starterModalPending).toBe(false);
    expect(getSuperAdminNuxPath()).toBe("/super-admin/setup");
  });

  it("can jump to a specific NUX step for testing", () => {
    setSuperAdminNuxStep("tos");
    expect(getSuperAdminNuxStep()).toBe("tos");
    expect(getSuperAdminNuxPath()).toBe("/install/tos");

    setSuperAdminNuxStep("account");
    expect(getSuperAdminNuxStep()).toBe("account");

    setSuperAdminNuxStep("company");
    expect(getSuperAdminNuxStep()).toBe("company");
    expect(getSuperAdminNuxPath()).toBe("/install/company");

    setSuperAdminNuxStep("org");
    expect(getSuperAdminNuxStep()).toBe("org");
    expect(getSuperAdminNuxPath()).toBe("/install/org");
    expect(readSuperAdminNux().account?.email).toBe("me@acme.org");
    expect(readSuperAdminNux().company?.name).toBe("Acme");

    setSuperAdminNuxStep("done");
    expect(getSuperAdminNuxStep()).toBe("done");
    expect(getSuperAdminNuxPath()).toBe("/super-admin/setup");
    expect(readSuperAdminNux().org?.name).toBe("My Organization");

    setSuperAdminNuxStep("welcome");
    expect(getSuperAdminNuxStep()).toBe("welcome");
  });
});
