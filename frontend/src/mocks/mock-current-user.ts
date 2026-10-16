export interface MockCurrentUser {
  userId: string;
  email: string;
}

const DEFAULT_MOCK_USER: MockCurrentUser = {
  userId: "99",
  email: "me@acme.org",
};

const SESSION_KEY = "openhands_mock_current_user";
let currentUser = DEFAULT_MOCK_USER;

export function getMockCurrentUser(): MockCurrentUser {
  try {
    const stored = sessionStorage.getItem(SESSION_KEY);
    if (stored) {
      currentUser = JSON.parse(stored) as MockCurrentUser;
    }
  } catch {
    // MSW can also run in environments without browser storage.
  }
  return currentUser;
}

export function setMockCurrentUser(user: MockCurrentUser) {
  currentUser = user;
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(user));
  } catch {
    // Keep the in-memory identity when browser storage is unavailable.
  }
}

export function resetMockCurrentUser() {
  currentUser = DEFAULT_MOCK_USER;
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Keep the in-memory reset when browser storage is unavailable.
  }
}
