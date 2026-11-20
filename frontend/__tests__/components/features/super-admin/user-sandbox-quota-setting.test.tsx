import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserSandboxQuotaSetting } from "#/components/features/super-admin/user-sandbox-quota-setting";
import { openHands } from "#/api/open-hands-axios";
import { QUOTA_QUERY_KEYS } from "#/hooks/query/use-quota-status";

vi.mock("#/api/open-hands-axios", () => ({
  openHands: { get: vi.fn(), put: vi.fn() },
}));
const get = vi.mocked(openHands.get);
const put = vi.mocked(openHands.put);
const url = "/api/admin/quota/users/user-1/quota";
function setup(limit: number | null = 25, readError = false) {
  get.mockResolvedValue({
    data: { user_id: "user-1", daily_sandbox_limit: limit },
  });
  if (readError) get.mockRejectedValue(new Error("offline"));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <UserSandboxQuotaSetting userId="user-1" />
    </QueryClientProvider>,
  );
  return { events: userEvent.setup(), client };
}

describe("UserSandboxQuotaSetting", () => {
  beforeEach(() => vi.resetAllMocks());
  it.each([null, -1, 25])(
    "loads the actual saved override %s",
    async (limit) => {
      setup(limit);
      expect(await screen.findByRole("combobox")).toHaveValue(
        limit === null ? "inherit" : limit === -1 ? "unlimited" : "custom",
      );
      expect(get).toHaveBeenCalledWith(url);
      expect(
        screen.getByRole("button", { name: "BUTTON$SAVE" }),
      ).toBeDisabled();
      if (limit === 25) expect(screen.getByRole("textbox")).toHaveValue("25");
    },
  );
  it.each(["", "0", "-2", "1.5", "1e3", "2147483648", "abc"])(
    "rejects invalid custom limit %s",
    async (value) => {
      const { events } = setup();
      const input = await screen.findByRole("textbox");
      await events.clear(input);
      if (value) await events.type(input, value);
      expect(
        screen.getByRole("button", { name: "BUTTON$SAVE" }),
      ).toBeDisabled();
      expect(put).not.toHaveBeenCalled();
    },
  );
  it.each([
    ["inherit", null],
    ["unlimited", -1],
    ["custom", 2147483647],
  ] as const)("saves %s and refreshes caches", async (mode, value) => {
    const { events, client } = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    put.mockImplementation(async () => {
      get.mockResolvedValue({
        data: { user_id: "user-1", daily_sandbox_limit: value },
      });
      return { data: { user_id: "user-1", daily_sandbox_limit: value } };
    });
    await events.selectOptions(await screen.findByRole("combobox"), mode);
    if (mode === "custom") {
      await events.clear(screen.getByRole("textbox"));
      await events.type(screen.getByRole("textbox"), String(value));
    }
    await events.click(screen.getByRole("button", { name: "BUTTON$SAVE" }));
    expect(put).toHaveBeenCalledWith(url, { daily_sandbox_limit: value });
    await screen.findByText("SUPER_ADMIN$SANDBOX_QUOTA_SAVED");
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: QUOTA_QUERY_KEYS.status,
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["super-admin", "users"],
    });
  });
  it("keeps editing disabled while saving and retains the draft on failure", async () => {
    const { events } = setup();
    let reject!: (error: Error) => void;
    put.mockReturnValue(
      new Promise((_, fail) => {
        reject = fail;
      }),
    );
    await events.selectOptions(
      await screen.findByRole("combobox"),
      "unlimited",
    );
    await events.click(screen.getByRole("button", { name: "BUTTON$SAVE" }));
    expect(screen.getByRole("combobox")).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "SUPER_ADMIN$SANDBOX_QUOTA_SAVING" }),
    ).toBeDisabled();
    reject(new Error("forbidden"));
    await screen.findByText("SUPER_ADMIN$SANDBOX_QUOTA_SAVE_ERROR");
    expect(screen.getByRole("combobox")).toHaveValue("unlimited");
    expect(screen.getByRole("button", { name: "BUTTON$SAVE" })).toBeEnabled();
  });
  it("never assumes inheritance on a failed read and supports retry", async () => {
    const { events } = setup(25, true);
    await screen.findByText("SUPER_ADMIN$SANDBOX_QUOTA_LOAD_ERROR");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    get.mockResolvedValue({
      data: { user_id: "user-1", daily_sandbox_limit: -1 },
    });
    await events.click(
      screen.getByRole("button", { name: "CONVERSATION$RETRY" }),
    );
    await waitFor(() =>
      expect(screen.getByRole("combobox")).toHaveValue("unlimited"),
    );
  });
});
