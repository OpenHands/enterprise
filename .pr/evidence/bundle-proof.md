# Why the preview verifier sees pre-fix code (and why this PR's fix is correct)

The preview verifier inspected the **`/canvas`** bundle and found pre-fix code
(`window.open(t,"_blank")` with no `noopener`, no protocol allow-list). That
bundle is the **Agent Canvas SPA**, built from the upstream OSS repo
`OpenHands/OpenHands` at pinned tag **`v1.35.0`** (see
`frontend/scripts/build-agent-canvas.sh`, `CANVAS_REF=v1.35.0`). It is a
**separate frontend** from the one this PR edits.

OSS PR [OpenHands/OpenHands#19229](https://github.com/OpenHands/OpenHands/pull/19229)
is merged to OSS `main` (merge commit `0474345a8c8fcd1a9964e66bac6812cf7ac4ba5f`)
but is **not in the `v1.35.0` tag** yet, so the served `/canvas` bundle correctly
still has pre-fix code. The issue explicitly states this is out of scope:
*"The OSS PR is not in a tagged release yet; this OHE change does not need the
pin bump."* and P10 *"this is the legacy OHE conversation card. The bundle bump
does not reach it."*

## What this PR actually changes

The **legacy OHE conversation card**, served from this repo's own `frontend/`
build at `/` (the `./frontend/build` SPA mounted by `saas_server.py` /
`openhands/app_server/app.py`). The changed files are:

- `frontend/src/utils/vscode-url-helper.ts`
- `frontend/src/components/features/conversation-panel/conversation-card/conversation-card.tsx`

These are compiled into the OHE SPA chunk `conversation-*.js` (e.g.
`conversation-Doant9s6.js`), NOT into the `/canvas` bundle.

## Proof the fix is in the served OHE bundle

Built the OHE frontend (`cd frontend && npm run build`) and grepped the output
chunks in `frontend/build/assets/`:

### 1. Protocol allow-list is present in the card chunk
```
$ grep -oE 'new Set\(\["http:","https:"\]\)' build/assets/conversation-Doant9s6.js
new Set(["http:","https:"])
```

### 2. window.open uses noopener,noreferrer in the card chunk
```
$ grep -oE 'window\.open\([^)]*noopener,noreferrer[^)]*\)' build/assets/conversation-Doant9s6.js
window.open(l,"_blank","noopener,noreferrer")
```

### 3. The OHE root-layout chunk does NOT contain the sync `e.vscode_url` handler
The verifier reported `root-layout-*.js` with `if(e.vscode_url){let t=st(e.vscode_url);t&&window.open(t,"_blank")}`.
That is the **Canvas** bundle's root-layout, not OHE's. The OHE root-layout
chunk contains zero `vscode_url` references:
```
$ grep -c 'vscode_url' build/assets/root-layout-e7qLxVhU.js
0
```
The OHE card fetches the URL asynchronously (`await ConversationService.getVSCodeUrl(...)`),
which is why the served Canvas code (sync `e.vscode_url`) does not match this
PR's source — they are two different components.

## Why the verifier could not reach the OHE card in the preview

The OHE SPA redirects `/` → `/canvas` (`frontend/src/routes/root-landing.tsx`,
`window.location.replace(...)`). The preview login lands on `/` and is
redirected to Canvas, so the verifier never opened the OHE sidebar. The OHE
conversation panel (and thus `ConversationCard`) is reachable on OHE routes
that render the sidebar (e.g. `/settings`, `/conversations/:id`), via the
`ConversationPanelButton` in `frontend/src/components/features/sidebar/sidebar.tsx`.
**Correction:** the sidebar panel renders `ConversationCard` without
`showOptions`, so the card's "Download via VS Code" item is never shown there
(`conversation-card-actions.tsx`: `conversationId && showOptions ? ... : undefined`).
The UI-reachable path for this change is the VS Code tab on
`/conversations/:id`, which runs the same `transformVSCodeUrl` via
`useUnifiedVSCodeUrl`. See "How to verify on a preview" in the PR description.

## Verification surface

This is a security hardening of a URL helper + a single `window.open` call
site, with no new UI. It is covered by unit tests, which pass:

```
cd frontend && npx vitest run __tests__/utils/vscode-url-helper.test.ts --no-coverage
Test Files  1 passed (1)
Tests       9 passed (9)
```

The tests assert every acceptance criterion in the issue:
- `javascript:`, `data:`, `file:` and unparsable URLs → `null` (not opened).
- `http(s)` URLs pass through; the `localhost → current hostname` rewrite still applies.
- The card call site opens with `"noopener,noreferrer"` (proven in the built bundle above).

## Conclusion

The FAIL was caused by inspecting the `/canvas` (OSS v1.35.0) bundle instead of
the OHE legacy frontend bundle this PR modifies. The fix is correctly compiled
into the OHE bundle served at `/`. No code change is required to address the
FAIL; this file documents the evidence.
