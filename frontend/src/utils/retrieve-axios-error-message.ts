import { AxiosError } from "axios";
import {
  isAxiosErrorWithDetailField,
  isAxiosErrorWithErrorField,
  isAxiosErrorWithMessageField,
  isAxiosErrorWithStructuredDetail,
  isAxiosErrorWithValidationDetail,
} from "./type-guards";

/**
 * "Password: String should have at least 8 characters" for a FastAPI
 * validation error on `["body", "password"]`.
 */
const formatValidationError = ({
  loc,
  msg,
}: {
  loc?: (string | number)[];
  msg: string;
}) => {
  const field = loc
    ?.filter((part): part is string => typeof part === "string")
    .filter((part) => !["body", "query", "path", "header"].includes(part))
    .at(-1);
  if (!field) {
    return msg;
  }
  const label = field.replaceAll("_", " ");
  return `${label.charAt(0).toUpperCase()}${label.slice(1)}: ${msg}`;
};

/**
 * Retrieve the error message from an Axios error
 * @param error The error to render a toast for
 */
export const retrieveAxiosErrorMessage = (error: AxiosError) => {
  let errorMessage: string | null = null;

  if (isAxiosErrorWithErrorField(error) && error.response?.data.error) {
    errorMessage = error.response?.data.error;
  } else if (
    isAxiosErrorWithDetailField(error) &&
    error.response?.data.detail
  ) {
    errorMessage = error.response.data.detail;
  } else if (isAxiosErrorWithStructuredDetail(error)) {
    errorMessage = error.response?.data.detail.message ?? error.message;
  } else if (isAxiosErrorWithValidationDetail(error)) {
    // Only the first failing field: one clear message rather than a list.
    const [firstError] = error.response?.data.detail ?? [];
    errorMessage = firstError
      ? formatValidationError(firstError)
      : error.message;
  } else if (
    isAxiosErrorWithMessageField(error) &&
    error.response?.data.message
  ) {
    errorMessage = error.response?.data.message;
  } else {
    errorMessage = error.message;
  }

  return errorMessage;
};
