import { describe, expect, it } from "vitest";
import {
  buildSetupChecklist,
  resolveSetupPersona,
  type OrgSetupPredicates,
} from "#/utils/org/setup-readiness";

const empty: OrgSetupPredicates = {
  hasOrgIdentity: false,
  hasLlm: false,
  hasIntegration: false,
  hasAutomation: false,
  hasTeammates: false,
  hasGit: false,
  hasConversation: false,
  hasSaml: false,
  setupFinished: false,
};

describe("setup-readiness", () => {
  it("resolves personas with Super Admin taking priority", () => {
    expect(
      resolveSetupPersona({ isInstanceSuperAdmin: true, role: "member" }),
    ).toBe("super_admin");
    expect(
      resolveSetupPersona({ isInstanceSuperAdmin: false, role: "owner" }),
    ).toBe("owner");
    expect(
      resolveSetupPersona({ isInstanceSuperAdmin: false, role: "admin" }),
    ).toBe("admin");
    expect(
      resolveSetupPersona({ isInstanceSuperAdmin: false, role: "member" }),
    ).toBe("member");
  });

  it("omits completed org steps for a new admin", () => {
    const checklist = buildSetupChecklist("admin", {
      ...empty,
      hasLlm: true,
      hasIntegration: true,
      hasAutomation: true,
    });

    expect(checklist.remaining.map((s) => s.id)).toEqual(["invite"]);
    expect(checklist.completed.map((s) => s.id)).toEqual([
      "llm",
      "integration",
      "automation",
    ]);
  });

  it("includes owner-only org identity when incomplete", () => {
    const checklist = buildSetupChecklist("owner", empty);
    expect(checklist.remaining.map((s) => s.id)).toContain("org_identity");
    expect(
      buildSetupChecklist("admin", empty).remaining.map((s) => s.id),
    ).not.toContain("org_identity");
  });

  it("defers Super Admin invite until core readiness", () => {
    const before = buildSetupChecklist("super_admin", {
      ...empty,
      hasOrgIdentity: true,
      hasLlm: true,
      hasIntegration: true,
    });
    expect(before.remaining.map((s) => s.id).at(-1)).toBe("invite");
    expect(before.nextStep?.id).toBe("automation");

    const after = buildSetupChecklist("super_admin", {
      ...empty,
      hasOrgIdentity: true,
      hasLlm: true,
      hasIntegration: true,
      hasAutomation: true,
    });
    expect(after.nextStep?.id).toBe("invite");
  });

  it("keeps member checklist minimal", () => {
    const checklist = buildSetupChecklist("member", empty);
    expect(checklist.remaining.map((s) => s.id)).toEqual([
      "git",
      "llm_confirm",
      "first_conversation",
    ]);
  });
});
