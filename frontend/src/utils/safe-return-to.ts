export function getSafeReturnTo(params: URLSearchParams): string {
  const destination = params.get("returnTo") || params.get("redirect") || "/";
  if (
    !destination.startsWith("/") ||
    destination.startsWith("//") ||
    destination.includes("\\") ||
    Array.from(destination).some((character) => character.charCodeAt(0) < 32)
  )
    return "/";
  return destination;
}
