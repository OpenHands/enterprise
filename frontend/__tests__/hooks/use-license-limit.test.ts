import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useLicenseLimitGuard } from "#/hooks/use-license-limit";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import type { LicenseUsage } from "#/api/license-service/license-service.api";

const mocks = vi.hoisted(() => ({
  usage: undefined as LicenseUsage | undefined,
}));

vi.mock("#/hooks/query/use-license-usage", () => ({
  useLicenseUsage: () => ({ data: mocks.usage }),
}));
vi.mock("#/utils/custom-toast-handlers");

function usage(used: number, limit: number | null): LicenseUsage {
  return {
    users: { used, limit },
    orgs: { used: 1, limit: 1 },
    customer_center_url: "https://customercenter.openhands.dev",
  };
}

describe("useLicenseLimitGuard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.usage = undefined;
  });

  it.each([
    ["under the limit", usage(3, 10)],
    ["with no user limit", usage(40, null)],
    ["before the usage loads", undefined],
  ])("runs the action %s", (_, current) => {
    mocks.usage = current;
    const action = vi.fn();
    const { result } = renderHook(() => useLicenseLimitGuard("users"));

    result.current(action)();

    expect(action).toHaveBeenCalledOnce();
    expect(displayErrorToast).not.toHaveBeenCalled();
  });

  it.each([
    ["users", "LICENSE$USER_LIMIT_REACHED"],
    ["orgs", "LICENSE$ORG_LIMIT_REACHED"],
  ] as const)(
    "shows the %s limit error at the limit and skips the action",
    (kind, message) => {
      mocks.usage = usage(10, 10);
      const action = vi.fn();
      const { result } = renderHook(() => useLicenseLimitGuard(kind));

      result.current(action)();

      expect(action).not.toHaveBeenCalled();
      expect(displayErrorToast).toHaveBeenCalledWith(message);
    },
  );
});
