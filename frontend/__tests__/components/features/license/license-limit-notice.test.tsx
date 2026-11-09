import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  LicenseLimitNotice,
  isAtLicenseLimit,
} from "#/components/features/license/license-limit-notice";
import type { LicenseUsage } from "#/api/license-service/license-service.api";

const mocks = vi.hoisted(() => ({
  usage: undefined as LicenseUsage | undefined,
}));

vi.mock("#/hooks/query/use-license-usage", () => ({
  useLicenseUsage: () => ({ data: mocks.usage }),
}));

function usage(
  used: number,
  limit: number | null,
  orgs = { used: 0, limit: null as number | null },
): LicenseUsage {
  return {
    users: { used, limit },
    orgs,
    customer_center_url: "https://customercenter.openhands.dev",
  };
}

describe("LicenseLimitNotice", () => {
  beforeEach(() => {
    mocks.usage = undefined;
  });

  it("renders nothing when the license has no user limit", () => {
    mocks.usage = usage(40, null);

    render(<LicenseLimitNotice kind="users" />);

    expect(
      screen.queryByTestId("license-limit-notice-users"),
    ).not.toBeInTheDocument();
  });

  it("renders nothing under the limit", () => {
    mocks.usage = usage(3, 10);

    render(<LicenseLimitNotice kind="users" />);

    expect(
      screen.queryByTestId("license-limit-notice-users"),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["at", 10],
    ["over", 15],
  ])(
    "says the limit is reached and links to upgrading %s the limit",
    (_, used) => {
      mocks.usage = usage(used, 10);

      render(<LicenseLimitNotice kind="users" />);

      expect(
        screen.getByTestId("license-limit-notice-users"),
      ).toHaveTextContent("LICENSE$USER_LIMIT_REACHED");
      expect(
        screen.getByRole("link", { name: "LICENSE$UPGRADE" }),
      ).toHaveAttribute("href", "https://customercenter.openhands.dev");
    },
  );

  it("says the organization limit is reached", () => {
    mocks.usage = usage(1, 10, { used: 1, limit: 1 });

    render(<LicenseLimitNotice kind="orgs" />);

    expect(screen.getByTestId("license-limit-notice-orgs")).toHaveTextContent(
      "LICENSE$ORG_LIMIT_REACHED",
    );
  });
});

describe("isAtLicenseLimit", () => {
  it.each([
    [usage(1, null), false],
    [usage(9, 10), false],
    [usage(10, 10), true],
    [usage(15, 10), true],
  ])("is right for %o", (value, expected) => {
    expect(isAtLicenseLimit(value, "users")).toBe(expected);
  });

  it("is false before the usage loads", () => {
    expect(isAtLicenseLimit(undefined, "orgs")).toBe(false);
  });
});
