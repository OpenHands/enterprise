import { http, HttpResponse } from "msw";
import { GitUser } from "#/types/git";
import { getMockCurrentUser } from "./mock-current-user";

const getGitUser = (): GitUser => {
  const currentUser = getMockCurrentUser();
  return {
    id: currentUser.userId,
    login: currentUser.email.split("@")[0],
    avatar_url: "https://avatars.githubusercontent.com/u/583231?v=4",
    company: "GitHub",
    email: currentUser.email,
    name: currentUser.email.split("@")[0],
  };
};

export const AUTH_HANDLERS = [
  http.get("/api/user/info", () => {
    const user = getGitUser();

    return HttpResponse.json(user);
  }),

  http.get("/api/v1/users/git-info", () => {
    const user = getGitUser();

    return HttpResponse.json(user);
  }),

  http.get("/api/onboarding_status", () =>
    HttpResponse.json({ should_complete_onboarding: false }),
  ),

  http.post("/api/authenticate", async () =>
    HttpResponse.json({ message: "Authenticated" }),
  ),

  http.post("/api/logout", () => HttpResponse.json(null, { status: 200 })),
];
