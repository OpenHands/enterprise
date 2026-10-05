# UI catalog reviewer notes

Snapshot of the reviewer notes from `frontend/src/routes/ui-catalog.tsx` at `095ca1955`. The catalog was removed in OHE-3488. Some notes are stale: they describe the prototype before the PR #621 sub-tickets landed.

## First-time Super Admin onboarding (OHE-3384 · PRD OHE-651)

Install wizard, in order: welcome, company, first organization, then org LLM and the starter modal. The acceptance criteria also say the organization is created in the background with the Super Admin as owner. This wizard still asks them to name it. Unit tests, the feature flag, the OpenHands-Cloud E2E, and the bug bash are not screens on this page.

### Welcome (page)

- Source: `frontend/src/routes/super-admin-install-welcome.tsx`
- Needs review: First-install NUX
- Notes: Confirm Next only writes the oh-sa-nux welcome flag in this browser. A finished install must not return here unless that storage is cleared.

### Company (page)

- Source: `frontend/src/routes/super-admin-install-company.tsx`
- Needs review: First-install NUX
- Notes: Confirm the name and logo are saved and the logo shows for other users.

### Organization (page)

- Source: `frontend/src/routes/super-admin-install-org.tsx`
- Needs review: First-install NUX
- Notes: This should run only after welcome and company are done, then open LLM settings with the starter modal pending. It must not skip to the dashboard.

### Language Model (page)

- Source: `frontend/src/routes/llm-settings.tsx`
- Needs review: First-install NUX
- Notes: The starter modal should open only while starterModalPending is set, and closing it must clear that flag. Check the profile rows at a narrow width.

### Starter setup (modal)

- Source: `frontend/src/components/features/super-admin/super-admin-group-setup-modal.tsx`
- Needs review: First-install NUX
- Notes: Skip must clear starterModalPending. Hovering another step must not collapse the pinned one. The catalog forceOpen path must not clear a real user's nux.

## Onboarding guide to the aha moment (OHE-3384 · PRD OHE-651)

After install, the setup guide follows the Super Admin across the product: LLM, automation template, MCP integration, invite, then optional SAML. It is for the first organization they own. A new admin of an organization that is already configured should not see it. The automation template step opens the templates page in Agent Canvas. The server checks each step against the organization's real LLM profiles, MCP servers, automations, members and invitations, and saves dismissal, so progress is the same on every browser.

### Setup guide (page)

- Source: `frontend/src/components/features/super-admin/super-admin-setup-guide.tsx`
- Notes: Confirm a hovered step does not replace the pinned step. Dismiss should hide the guide without clearing oh-sa-nux, and Start should bring it back.

### Remove setup guide (modal)

- Source: `frontend/src/components/features/super-admin/super-admin-setup-guide.tsx`
- Notes: Dismiss should only set the server's guide_dismissed flag. It must not clear oh-sa-nux or org data.

### Integrations (page)

- Source: `frontend/src/components/features/settings/integrations/legacy-resolvers-page.tsx`
- Notes: Confirm hub routes redirect here and ENABLE_INTEGRATIONS_HUB is not on this branch. Resolver connect should still work for each provider.

### Configure integration (modal)

- Source: `frontend/src/components/features/settings/integrations/integration-modal.tsx`
- Notes: This is the legacy resolver form. Confirm the token or webhook is not shown again after save, and that no hub connector is mounted.

### MCP (page)

- Source: `frontend/src/routes/mcp-settings.tsx`
- Notes: Confirm the display name is not written back as the config key. A short name can match the wrong logo because matching is a substring.

### Add MCP server (modal)

- Source: `frontend/src/components/features/settings/mcp-settings/mcp-server-modal.tsx`
- Notes: Add and edit share this dialog. Confirm the saved key is the name field, not the capitalized display label.

### Delete MCP server (modal)

- Source: `frontend/src/routes/mcp-settings.tsx`
- Notes: Confirm deletion removes the stored config key, even when the row shows a display name.

### Members (page)

- Source: `frontend/src/routes/manage-organization-members.tsx`
- Notes: Do not expect a first or last name. Invite, role change, and remove still use email and role. Removal must keep the last-owner rule.

### Invite members (modal)

- Source: `frontend/src/components/features/org/invite-organization-member-modal.tsx`
- Notes: Existing membership action. Confirm the neo dialog still submits email and role, and does not ask for a name.

### Change member role (modal)

- Source: `frontend/src/components/features/org/confirm-update-role-modal.tsx`
- Notes: Existing confirm. The submitted role should be the one shown, including owner, admin, and member.

### Remove member (modal)

- Source: `frontend/src/components/features/org/confirm-remove-member-modal.tsx`
- Notes: Existing confirm. Removal must still refuse to drop the last owner.

## Super Admin dashboard (OHE-3433 · PRD OHE-651)

Opened from the org menu in settings when the viewer is a Super Admin. Then create organizations, grant and revoke Super Admins, provision and manage users, and edit org membership. Delete and disable live on Manage user. SSO is not configurable here, and Instance Settings has no documentation link for the SSO environment variable. Email and auto-org on that page are read-only. The gate is ENABLE_SUPER_ADMIN, accepting true or 1. The OpenHands-Cloud E2E and the bug bash are still outside this UI.

### Dashboard (page)

- Source: `frontend/src/components/features/super-admin/super-admin-dashboard.tsx`
- Needs review: Dashboard
- Notes: Confirm ENABLE_SUPER_ADMIN accepts both true and 1, and a normal org member is denied. Review empty, error, and stop-conversation states more than layout. The shield belongs only on /super-admin.

### Organizations (page)

- Source: `frontend/src/components/features/super-admin/super-admin-pages.tsx`
- Needs review: Org status
- Notes: Review migration 144. Existing orgs must default to active. Suspend has to block usage, resume has to undo it, and callers must not ignore org.status.

### Create organization (modal)

- Source: `frontend/src/components/features/org/create-organization-modal.tsx`
- Needs review: Membership
- Notes: Confirm the owner and contact email are the values that get stored, and the org is created active.

### Users (page)

- Source: `frontend/src/components/features/super-admin/super-admin-pages.tsx`
- Needs review: Provisioning
- Notes: Confirm only a Super Admin can provision, the role is stored per org, and the returned API key is not logged. Cross-org writes go through the membership service.

### Provision User (modal)

- Source: `frontend/src/components/features/super-admin/super-admin-pages.tsx`
- Needs review: Provisioning
- Notes: Confirm who can call provision, that each selected org stores its own role, and that a failed call does not show credentials.

### Provision credentials (modal)

- Source: `frontend/src/components/features/super-admin/super-admin-pages.tsx`
- Needs review: Provisioning
- Notes: The LiteLLM key is plain text. Confirm it is not logged or written to localStorage, and that closing the dialog removes it from the page.

### Invite by email (modal)

- Source: `frontend/src/components/features/org/invite-organization-member-modal.tsx`
- Needs review: Membership
- Notes: Confirm a Super Admin who is not a member can invite, personal workspaces are not offered, and the link works for someone with no account.

### Manage user (modal)

- Source: `frontend/src/components/features/super-admin/super-admin-user-groups-modal.tsx`
- Needs review: Membership
- Notes: Review cross-org add, remove, and role changes. A Super Admin must not remove the last owner of an org.

### Grant yourself access (modal)

- Source: `frontend/src/components/features/super-admin/super-admin-grant-self-access-modal.tsx`
- Needs review: Membership
- Notes: Opening an org without membership must stay blocked until this completes, and the role written must be the one selected.

### Super Admins (page)

- Source: `frontend/src/components/features/super-admin/super-admin-pages.tsx`
- Needs review: Access
- Notes: Grant and revoke must require manage_super_admins. Confirm this route cannot bypass the Super Admin access check, and an org admin cannot open it.

### Grant Super Admin (modal)

- Source: `frontend/src/components/features/super-admin/super-admin-pages.tsx`
- Needs review: Access
- Notes: A caller without manage_super_admins must be rejected. Confirm granting does not also skip org membership checks for that user.

### Instance Settings (page)

- Source: `frontend/src/components/features/super-admin/super-admin-pages.tsx`
- Needs review: Dashboard
- Notes: Review the permission-denied and error states. Logo and email status must stay behind Super Admin access.

## Neo theme on the screens these flows land in (Settings chrome · this branch)

Not its own product ticket. Visual pass on the settings screens around the two sprints. Review layout and save behavior, not access or provisioning.

### Dashboard (page)

- Source: `frontend/src/components/features/admin-dashboard/admin-dashboard.tsx`
- Notes: Visual regression on the usage widgets and skeletons. No access or billing logic changed on this page.

### Budgets (page)

- Source: `frontend/src/components/features/budgets/budgets.tsx`
- Notes: Visual regression on budget tabs and alerts. Confirm limits still save the same way.

### Condenser (page)

- Source: `frontend/src/routes/org-default-condenser-settings.tsx`
- Notes: Visual and nav-order check. Confirm condenser settings still save, and the ACP disable still applies if it did before.

### Verification (page)

- Source: `frontend/src/routes/verification-settings.tsx`
- Notes: Visual and nav-order check. Confirm verification still runs against the org LLM setup.

### Billing & Credits (page)

- Source: `frontend/src/routes/credits.tsx`
- Notes: Visual pass. This is not the provisioned LiteLLM key. Confirm plan and credit actions still submit.

### Organization (page)

- Source: `frontend/src/routes/manage-org.tsx`
- Notes: Confirm Create organization in the org menu does not break org switching. Delete here is the current org, not Super Admin suspend.

### Change organization name (modal)

- Source: `frontend/src/components/features/org/change-org-name-modal.tsx`
- Notes: Existing rename. Confirm the saved name is the field value and the org menu updates after save.

### Delete organization (modal)

- Source: `frontend/src/components/features/org/delete-org-confirmation-modal.tsx`
- Notes: Existing delete. This is not Super Admin suspend. Confirm it deletes only the current org and still requires the confirm step.

### Agent (page)

- Source: `frontend/src/routes/agent-settings.tsx`
- Notes: Visual pass on the neo chrome. Confirm agent settings still save with the existing form.

### API Keys (page)

- Source: `frontend/src/components/features/settings/api-keys-manager.tsx`
- Notes: Visual pass. Keys should still save immediately. This is separate from the LiteLLM key returned by provisioning.

### Secrets (page)

- Source: `frontend/src/routes/secrets-settings.tsx`
- Notes: Visual pass. Adding and deleting a secret should still hit the secrets API immediately, with values masked after save.

### Skills (page)

- Source: `frontend/src/routes/skills-settings.tsx`
- Notes: Visual pass. Confirm adding and removing a skill still saves immediately.
