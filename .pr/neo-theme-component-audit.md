# Neo theme component audit (enterprise frontend)

OpenHands-Neo token plumbing is live (`--oh-*`, `--cool-grey-*`, `applyColorTheme`).
Hardcoded hex leftovers still diverge from neo on many surfaces.

## Done in this pass

- Settings left nav tokens (`settings-nav-link` / header / divider)
- Context menu shell (`context-menu-container`)
- User avatar muted icon
- Settings nav logo header + bottom user menu (docs + logout)
- Org settings page (`manage-org` + credits/add chips, org name field, delete CTA, git routing list) via canvas `form-control-classes` / `settings-list-classes`
- Org members list (`manage-organization-members` + member/pending rows + search + pagination) via canvas `settings-list-classes` / `form-control-classes`

## Still needs tokenization (priority)

### P0 — chrome users see every session

| Area | Files |
|------|-------|
| Settings inputs | `settings-input.tsx`, `settings-dropdown-input.tsx`, `badge-input.tsx` (`#717888`, `#2D2F36`) |
| Dropdown shell | `ui/dropdown/dropdown.tsx`, `model-selector.tsx` |
| Cards | `ui/card.tsx` (`#26282D`, `#727987`) |
| Org badge | `org-wide-settings-badge.tsx` |
| Context CTA | `context-menu-cta.tsx` |

### P1 — settings feature forms

- `marketplace-modal.tsx`, `mcp-server-form.tsx`, `secret-form.tsx`, `skills-table.tsx`
- `schema-field.tsx`, `acp-secret-field.tsx`, `configure-modal.tsx`, `jira-dc-integration-panel.tsx`
- `mobile-header.tsx`, `upgrade-banner-with-backdrop.tsx`, `pro-pill.tsx`

### P2 — app shell / home / conversation

- App sidebar button accents (`conversation-panel-button`, `admin-dashboard-button`)
- Home cards (`new-conversation`, `repo-connector`)
- Auth/CTA shells (`homepage-cta`, `login-cta`, `enterprise-card`)
- Conversation tabs (`#0D0F11`, `#9299AA`)
- Dropdown selected gold (`#C9B974` → `primary` / logo token)

### Done — Budgets

- `budgets.tsx`, `budgets-tabs.tsx`, `budgets-components.tsx` tokenized to neo / agent-canvas (`bg-base-secondary`, `bg-surface-deep`, `text-muted`, `bg-primary`, settings switch)

## Done — Usage & Monitoring

- `usage-dashboard.tsx`, `usage-dashboard-tabs.tsx`, `usage-dashboard-widgets.tsx`, `admin-dashboard.tsx` tokenized to neo / agent-canvas (tabs, time-window control, KPI cards, tables, inputs/selects, chart stroke via `--oh-color-primary`)

## Token map to prefer

| Legacy hex | Neo token / class |
|------------|-------------------|
| `#0D0F11` / `#050505` | `bg-base` / `bg-surface-deep` |
| `#26282D` | `bg-surface` / `bg-base-secondary` |
| `#717888` / `#727987` | `border-[var(--oh-border)]` / `border-border-input` |
| `#8C8C8C` | `text-muted` |
| `#242424` | `border-[var(--oh-border-subtle)]` |
| `#2D2F36` | `bg-tertiary` / cool-grey surface |
| `#C9B974` | `bg-primary` / `text-logo` (neo primary is white) |
