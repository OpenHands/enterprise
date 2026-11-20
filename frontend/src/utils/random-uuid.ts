/**
 * Random UUIDv4 that also works outside secure contexts.
 *
 * `crypto.randomUUID` is SecureContext-only, so it is undefined when the app
 * is served over plain HTTP from a non-loopback host. `crypto.getRandomValues`
 * is available in every context.
 */
export function randomUUID(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(
    "",
  );
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
