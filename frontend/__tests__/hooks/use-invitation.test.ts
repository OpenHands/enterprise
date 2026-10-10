import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as ToastHandlers from "#/utils/custom-toast-handlers";
import { useInvitationEmailMismatchStore } from "#/stores/invitation-email-mismatch-store";

const INVITATION_TOKEN_KEY = "openhands_invitation_token";

// Mock setSearchParams function
const mockSetSearchParams = vi.fn();

// Default mock searchParams
let mockSearchParamsData: Record<string, string> = {};

// Mock react-router
vi.mock("react-router", () => ({
  useSearchParams: () => [
    {
      get: (key: string) => mockSearchParamsData[key] || null,
      has: (key: string) => key in mockSearchParamsData,
    },
    mockSetSearchParams,
  ],
}));

// Import after mocking
import { useInvitation } from "#/hooks/use-invitation";

describe("useInvitation", () => {
  beforeEach(() => {
    // Clear localStorage before each test
    localStorage.clear();
    // Reset mock data
    mockSearchParamsData = {};
    mockSetSearchParams.mockClear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe("initialization", () => {
    it("should initialize with null token when localStorage is empty", () => {
      // Arrange - localStorage is empty (cleared in beforeEach)

      // Act
      const { result } = renderHook(() => useInvitation());

      // Assert
      expect(result.current.invitationToken).toBeNull();
      expect(result.current.hasInvitation).toBe(false);
    });

    it("should initialize with token from localStorage if present", () => {
      // Arrange
      const storedToken = "inv-stored-token-12345";
      localStorage.setItem(INVITATION_TOKEN_KEY, storedToken);

      // Act
      const { result } = renderHook(() => useInvitation());

      // Assert
      expect(result.current.invitationToken).toBe(storedToken);
      expect(result.current.hasInvitation).toBe(true);
    });
  });

  describe("URL token capture", () => {
    it("should capture invitation_token from URL and store in localStorage", () => {
      // Arrange
      const urlToken = "inv-url-token-67890";
      mockSearchParamsData = { invitation_token: urlToken };

      // Act
      renderHook(() => useInvitation());

      // Assert
      expect(localStorage.getItem(INVITATION_TOKEN_KEY)).toBe(urlToken);
      expect(mockSetSearchParams).toHaveBeenCalled();
    });
  });

  describe("completion cleanup", () => {
    it("should keep the token when the sign-in used another email", () => {
      // Arrange: the invitation is still pending after a wrong-account sign-in
      const storedToken = "inv-token-to-keep";
      localStorage.setItem(INVITATION_TOKEN_KEY, storedToken);
      mockSearchParamsData = { email_mismatch: "true" };

      // Act
      const { result } = renderHook(() => useInvitation());

      // Assert
      expect(localStorage.getItem(INVITATION_TOKEN_KEY)).toBe(storedToken);
      expect(result.current.invitationToken).toBe(storedToken);
      expect(mockSetSearchParams).toHaveBeenCalled();
    });

    it("should clear localStorage when invitation_success param is present", () => {
      // Arrange
      const storedToken = "inv-token-to-clear";
      localStorage.setItem(INVITATION_TOKEN_KEY, storedToken);
      mockSearchParamsData = { invitation_success: "true" };

      // Act
      renderHook(() => useInvitation());

      // Assert
      expect(localStorage.getItem(INVITATION_TOKEN_KEY)).toBeNull();
    });

    it("should clear localStorage when invitation_expired param is present", () => {
      // Arrange
      localStorage.setItem(INVITATION_TOKEN_KEY, "inv-token");
      mockSearchParamsData = { invitation_expired: "true" };

      // Act
      renderHook(() => useInvitation());

      // Assert
      expect(localStorage.getItem(INVITATION_TOKEN_KEY)).toBeNull();
    });
  });

  describe("callback outcome", () => {
    it.each([
      ["invitation_success", "success", "ORG$INVITATION_ACCEPTED"],
      ["already_member", "success", "ORG$ALREADY_MEMBER"],
      ["invitation_expired", "error", "ORG$INVITATION_EXPIRED"],
      ["invitation_invalid", "error", "ORG$INVITATION_INVALID"],
      ["invitation_error", "error", "ORG$INVITATION_ACCEPT_ERROR"],
    ] as const)(
      "should tell the user about %s when asked to report",
      (param, kind, message) => {
        // Arrange
        const successToast = vi.spyOn(ToastHandlers, "displaySuccessToast");
        const errorToast = vi.spyOn(ToastHandlers, "displayErrorToast");
        mockSearchParamsData = { [param]: "true" };

        // Act
        renderHook(() => useInvitation({ reportOutcome: true }));

        // Assert
        const shown = kind === "success" ? successToast : errorToast;
        const other = kind === "success" ? errorToast : successToast;
        expect(shown).toHaveBeenCalledExactlyOnceWith(message);
        expect(other).not.toHaveBeenCalled();
      },
    );

    it("should leave an email mismatch to the signed-in accept", () => {
      // Arrange
      const successToast = vi.spyOn(ToastHandlers, "displaySuccessToast");
      const errorToast = vi.spyOn(ToastHandlers, "displayErrorToast");
      mockSearchParamsData = { email_mismatch: "true" };

      // Act
      renderHook(() => useInvitation({ reportOutcome: true }));

      // Assert
      expect(successToast).not.toHaveBeenCalled();
      expect(errorToast).not.toHaveBeenCalled();
      expect(useInvitationEmailMismatchStore.getState().isOpen).toBe(false);
    });

    it("should not report the outcome unless asked to", () => {
      // Arrange
      const errorToast = vi.spyOn(ToastHandlers, "displayErrorToast");
      mockSearchParamsData = { invitation_expired: "true" };

      // Act
      renderHook(() => useInvitation());

      // Assert
      expect(errorToast).not.toHaveBeenCalled();
    });
  });

  describe("buildOAuthStateData", () => {
    it("should include invitation_token in OAuth state when token is present", () => {
      // Arrange
      const token = "inv-oauth-token-12345";
      localStorage.setItem(INVITATION_TOKEN_KEY, token);

      const { result } = renderHook(() => useInvitation());
      const baseState = { redirect_url: "/dashboard" };

      // Act
      const stateData = result.current.buildOAuthStateData(baseState);

      // Assert
      expect(stateData.invitation_token).toBe(token);
      expect(stateData.redirect_url).toBe("/dashboard");
    });

    it("should not include invitation_token when no token is present", () => {
      // Arrange - no token in localStorage

      const { result } = renderHook(() => useInvitation());
      const baseState = { redirect_url: "/dashboard" };

      // Act
      const stateData = result.current.buildOAuthStateData(baseState);

      // Assert
      expect(stateData.invitation_token).toBeUndefined();
      expect(stateData.redirect_url).toBe("/dashboard");
    });
  });

  describe("clearInvitation", () => {
    it("should remove token from localStorage when called", () => {
      // Arrange
      localStorage.setItem(INVITATION_TOKEN_KEY, "inv-token-to-clear");
      const { result } = renderHook(() => useInvitation());

      // Act
      act(() => {
        result.current.clearInvitation();
      });

      // Assert
      expect(localStorage.getItem(INVITATION_TOKEN_KEY)).toBeNull();
      expect(result.current.invitationToken).toBeNull();
      expect(result.current.hasInvitation).toBe(false);
    });
  });
});
