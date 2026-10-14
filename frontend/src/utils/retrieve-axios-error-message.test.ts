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

  it("names the field and reason of a FastAPI validation error", () => {
    const error = axiosError({
      detail: [
        {
          type: "string_too_short",
          loc: ["body", "password"],
          msg: "String should have at least 8 characters",
          input: "123456",
          ctx: { min_length: 8 },
        },
        {
          type: "value_error",
          loc: ["body", "email"],
          msg: "value is not a valid email address",
        },
      ],
    });

    expect(retrieveAxiosErrorMessage(error)).toBe(
      "Password: String should have at least 8 characters",
    );
  });

  it("shows a validation message without a field name when there is none", () => {
    const error = axiosError({
      detail: [{ type: "missing", loc: ["body"], msg: "Field required" }],
    });

    expect(retrieveAxiosErrorMessage(error)).toBe("Field required");
  });

  it("falls back to the Axios message for unknown response shapes", () => {
    expect(retrieveAxiosErrorMessage(axiosError({ unexpected: true }))).toBe(
      "Request failed",
    );
  });
});
