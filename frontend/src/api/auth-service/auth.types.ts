export interface AuthenticateResponse {
  message?: string;
  error?: string;
}

export interface GitHubAccessTokenResponse {
  access_token: string;
}

export interface AuthCapabilities {
  mode: "local" | "keycloak";
  password_login: boolean;
  login_providers: string[];
  registration: "admin_or_invitation";
  email_recovery: boolean;
  repository_connections: { manual_tokens: boolean; broker: boolean };
}

export interface AuthDestination {
  redirect_url: string;
  invitation_token?: string;
}

export interface PasswordLogin extends AuthDestination {
  email: string;
  password: string;
}

export interface PasswordChange extends AuthDestination {
  current_password: string;
  new_password: string;
}

export interface AuthResult {
  redirect_url: string;
  password_change_required?: boolean;
}

export interface CreateAccount {
  email: string;
  initial_password: string;
}

export interface CreatedAccount {
  id: string;
  email: string;
  password_change_required: boolean;
}
