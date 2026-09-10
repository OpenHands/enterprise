export function isValidNewPassword(password: string): boolean {
  const { length } = Array.from(password);
  return length >= 15 && length <= 1024;
}
