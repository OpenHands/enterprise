# Live evidence for head 024040a (masked secret textarea)

Frontend at commit `024040a`, run with `npm run dev:mock:saas` (MSW mock backend) and driven by Playwright
(Chromium headless shell 148 and Firefox 150). Path: Settings → Secrets → Add a new secret.

Value typed: `-----BEGIN KEY-----\nabc123def456\n-----END KEY-----`

| Step | DOM state of `[data-testid="value-input"]` | Toggle aria-label | Screenshot |
|------|--------------------------------------------|-------------------|------------|
| Masked (default) | `TEXTAREA`, `data-masked="true"`, `autocomplete="off"`, computed `-webkit-text-security: disc`, value keeps both `\n` | Show value | `01_multiline_masked.png` |
| Revealed (toggle click 1) | `data-masked="false"`, computed `-webkit-text-security: none`, same value | Hide value | `02_multiline_revealed.png` |
| Re-masked (toggle click 2) | `data-masked="true"`, computed `disc`, same value | Show value | — |
| Submit | Captured request: `POST /api/v1/secrets` body `{"name":"PEM_TEST_KEY","value":"-----BEGIN KEY-----\nabc123def456\n-----END KEY-----"}` | — | `03_saved_list.png` |
| Firefox 150, masked | computed `-webkit-text-security: disc`, every line rendered as dots | Show value | `04_firefox_masked.png` |

The backend is mocked, so the newline check stops at the request payload. The backend secrets router
(`openhands/app_server/secrets/`) stores the value as sent and does not strip it; this PR does not change the backend.
