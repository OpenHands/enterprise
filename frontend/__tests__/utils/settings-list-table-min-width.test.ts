import { describe, expect, it } from "vitest";
import { settingsListTableMinWidthStyle } from "#/utils/settings-list-classes";

describe("settingsListTableMinWidthStyle", () => {
  it("gives four-column tables enough room to collapse before scrolling", () => {
    expect(settingsListTableMinWidthStyle(4)).toEqual({ minWidth: "21rem" });
  });

  it("grows with additional columns", () => {
    expect(settingsListTableMinWidthStyle(6)).toEqual({ minWidth: "33rem" });
  });
});
