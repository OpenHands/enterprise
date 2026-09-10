import { http, HttpResponse } from "msw";
import { GitUser } from "#/types/git";

export const AUTH_HANDLERS = [
  http.get("/api/auth/capabilities", () =>
    HttpResponse.json({
      mode: "keycloak",
      password_login: false,
      login_providers: [
        "github",
        "gitlab",
        "bitbucket",
        "azure_devops",
        "enterprise_sso",
        "bitbucket_data_center",
      ],
      registration: "admin_or_invitation",
      email_recovery: true,
      repository_connections: { manual_tokens: false, broker: true },
    }),
  ),
  http.get("/api/auth/csrf", () =>
    HttpResponse.json({ csrf_token: "mock-csrf-token" }),
  ),
  http.get("/api/user/info", () => {
    const user: GitUser = {
      id: "1",
      login: "octocat",
      avatar_url: "https://avatars.githubusercontent.com/u/583231?v=4",
      company: "GitHub",
      email: "placeholder@placeholder.placeholder",
      name: "monalisa octocat",
    };

    return HttpResponse.json(user);
  }),

  http.post("/api/authenticate", async () =>
    HttpResponse.json({ message: "Authenticated" }),
  ),

  http.post("/api/logout", () => HttpResponse.json(null, { status: 200 })),
];
