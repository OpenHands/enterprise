import { AxiosError, AxiosHeaders } from "axios";
import { describe, expect, it } from "vitest";
import { retrieveAxiosErrorMessage } from "./retrieve-axios-error-message";

const axiosError = (data: unknown) =>
  new AxiosError("Request failed", undefined, undefined, undefined, {
    data,
    status: 429,
    statusText: "Too Many Requests",
    headers: {},
    config: { headers: new AxiosHeaders() },
  });

describe("retrieveAxiosErrorMessage", () => {
  it("extracts a message from structured FastAPI detail", () => {
    const error = axiosError({
      detail: {
        code: "daily_conversation_limit_reached",
        message: "Daily limit reached. Request more at /settings/quota.",
      },
    });

    expect(retrieveAxiosErrorMessage(error)).toBe(
      "Daily limit reached. Request more at /settings/quota.",
    );
  });

  it("falls back to the Axios message for unknown response shapes", () => {
    expect(retrieveAxiosErrorMessage(axiosError({ unexpected: true }))).toBe(
      "Request failed",
    );
  });
});
