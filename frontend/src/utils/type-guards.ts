import { AxiosError } from "axios";

export const isAxiosErrorWithErrorField = (
  error: AxiosError,
): error is AxiosError<{ error: string }> =>
  typeof error.response?.data === "object" &&
  error.response?.data !== null &&
  "error" in error.response.data &&
  typeof error.response?.data?.error === "string";

export const isAxiosErrorWithDetailField = (
  error: AxiosError,
): error is AxiosError<{ detail: string }> =>
  typeof error.response?.data === "object" &&
  error.response?.data !== null &&
  "detail" in error.response.data &&
  typeof error.response?.data?.detail === "string";

export const isAxiosErrorWithStructuredDetail = (
  error: AxiosError,
): error is AxiosError<{ detail: { message: string } }> => {
  const data = error.response?.data;
  if (typeof data !== "object" || data === null || !("detail" in data)) {
    return false;
  }

  const { detail } = data;
  return (
    typeof detail === "object" &&
    detail !== null &&
    "message" in detail &&
    typeof detail.message === "string"
  );
};

export const isAxiosErrorWithMessageField = (
  error: AxiosError,
): error is AxiosError<{ message: string }> =>
  typeof error.response?.data === "object" &&
  error.response?.data !== null &&
  "message" in error.response.data &&
  typeof error.response?.data?.message === "string";
