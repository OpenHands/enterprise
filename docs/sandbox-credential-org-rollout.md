# Sandbox credential organization binding rollout

New sandboxes persist the effective organization at launch. Resolver runs use
the explicit claimed organization when present; unclaimed-repository and
no-repository runs use their authenticated effective organization. Local
authentication without an organization remains unscoped. New conversations
may reuse a sandbox only in that organization, including grouping, explicit
`sandbox_id` requests and inherited parent sandbox IDs. In Enterprise, an unbound
legacy sandbox cannot be assigned to a new conversation.

When a sandbox webhook first records a conversation, its organization comes from
the matching owner's persisted sandbox binding. A later owner organization switch
therefore cannot put that conversation in a different organization from its
credential lookups. Existing conversation organization records are preserved.
For unbound or unrecorded sandboxes, metadata retains its previous current-org
fallback; this does not create a credential binding.

Apply migration 205 (after revision 204) before upgrading application writers. Complete the upgrade
of all application servers before considering the binding enforced; old writers
and lookup handlers do not enforce it during a mixed-version rollout. Coordinate
migration numbering and ancestry with other pending migrations before merging.

## Existing conversations

The migration binds an existing sandbox only from its own conversation records:
when every conversation its owner recorded on it names the same organization,
the sandbox is bound to that organization. A sandbox whose owner's conversations
name more than one organization, or that has none, stays unbound. The owner's
current organization is never used, because it is mutable.

A conversation recorded through the sandbox webhook before this change took its
organization from the owner's current organization at that moment, so a
backfilled binding is only as reliable as that record. It matches the
organization the conversation is listed under.

Existing sandboxes that stay unbound return 403 from both
`/api/v1/sandboxes/{id}/settings/secrets` and `.../{name}`. This also affects
paused conversations resumed after the upgrade. New sub-conversations inheriting
an unbound parent sandbox are refused too. If the owner switches organizations,
a child cannot inherit the original organization's sandbox from the new context. Recreate the sandbox/start a
new conversation in the intended organization to restore these lookups.

Affected consumers include the Azure DevOps git credential helper (so git
operations needing credentials, including push, can fail), extension skill
scripts that use these routes, and SDK `OpenHandsCloudWorkspace.get_secrets()`.
Automations or other jobs using existing sandboxes and these routes are affected
too. This is not limited to an interactive settings screen.

Credentials already copied into a running process or sandbox are not erased or
revoked by this change. The upgrade does not promise live refresh or revocation.

Support can distinguish lookup denials using the structured warning
`sandbox_credential_access_denied`, with sandbox, owner user and org IDs plus a reason
such as `unbound`, `unknown_owner`, `invalid_owner`, `not_a_member`, `inactive`, `disabled` or
`suspended`. Responses remain generic and logs do not include secret values.

Grouping skips incompatible sandboxes instead of reclaiming them. Plan capacity
for new sandboxes after rollout; existing sandboxes still consume capacity and
normal concurrent-sandbox limit handling still applies.

Remote daily quota accounting uses the same resolved organization as the new
sandbox binding. Resolver claim lookup currently returns no claim on lookup
errors as well as for unclaimed repositories. Those runs retain existing routing
to the authenticated default organization and now bind credentials there too.
Changing claim-lookup errors to fail closed is separate resolver-routing work.
