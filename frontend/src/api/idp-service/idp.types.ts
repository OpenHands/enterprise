export interface HasPasswordResponse {
  has_password: boolean;
}

export interface SetPasswordParams {
  /** Required only when the user already has a password set. */
  currentPassword?: string;
  newPassword: string;
  confirmPassword: string;
}

export interface SetPasswordResponse {
  message: string;
}
