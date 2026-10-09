import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SuperAdminGrantSelfAccessModal } from "#/components/features/super-admin/super-admin-grant-self-access-modal";

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("#/hooks/mutation/use-super-admin-mutations", () => ({
  useUpdateSuperAdminUserGroups: () => ({
    mutate,
    isPending: false,
  }),
}));

describe("SuperAdminGrantSelfAccessModal", () => {
  beforeEach(() => {
    mutate.mockClear();
  });

  it("defaults the access type to admin and grants that role", async () => {
    const events = userEvent.setup();
    const onGranted = vi.fn();
    render(
      <SuperAdminGrantSelfAccessModal
        orgId="3"
        orgName="Beta LLC"
        userId="99"
        onClose={vi.fn()}
        onGranted={onGranted}
      />,
    );

    expect(screen.getByTestId("super-admin-grant-self-access-role")).toHaveValue(
      "admin",
    );

    await events.click(
      screen.getByTestId("super-admin-grant-self-access-confirm"),
    );

    expect(mutate).toHaveBeenCalledWith(
      {
        userId: "99",
        action: "add",
        orgIds: ["3"],
        role: "admin",
      },
      { onSuccess: onGranted },
    );
  });

  it("can grant the owner role instead", async () => {
    const events = userEvent.setup();
    render(
      <SuperAdminGrantSelfAccessModal
        orgId="3"
        orgName="Beta LLC"
        userId="99"
        onClose={vi.fn()}
        onGranted={vi.fn()}
      />,
    );

    await events.selectOptions(
      screen.getByTestId("super-admin-grant-self-access-role"),
      "owner",
    );
    await events.click(
      screen.getByTestId("super-admin-grant-self-access-confirm"),
    );

    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ role: "owner" }),
      expect.any(Object),
    );
  });
});
