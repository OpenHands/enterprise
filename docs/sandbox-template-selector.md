# Organization sandbox selector (Helm)

The opt-in Agent Canvas home-page selector offers **Default** followed by named
image-and-size templates beside Open Repository and Plugins. A choice applies
only to the conversation being created. It never saves a user preference.

Define templates in `runtime-api.warmRuntimes.configsByName`, including `image`,
`display_name`, `resources.requests`, `resources.limits`, and `count`. Two templates
can use the same image with different resources. Use `count: 0` when the template
should be available without maintaining idle warm sandboxes.

Then configure the Enterprise application through Helm values:

```yaml
env:
  ENABLE_SANDBOX_SELECTOR: "true"
  SANDBOX_TEMPLATE_ORG_CONFIG: >-
    {
      "11111111-1111-4111-8111-111111111111": {
        "template_ids": ["v1_current", "monorepo_large"],
        "default_template_id": "v1_current"
      },
      "22222222-2222-4222-8222-222222222222": {
        "template_ids": ["testing_small", "testing_large"],
        "default_template_id": "testing_small"
      }
    }
```

Replace the example organization UUIDs and template IDs with actual values.
The application validates this value during configuration bootstrap. Invalid
JSON, organization UUIDs, or policy fields prevent the new process from starting;
they cannot leave it reporting readiness while requests fail later.
The default must be in that organization's nonempty, unique `template_ids` list.
These are stable template IDs, not free-form image URLs. The same Helm-defined
template can be shared by multiple organizations.

`ENABLE_SANDBOX_SELECTOR` defaults off and accepts `true` or `1`. The existing
feature-flag administration API can override it with organization-targeted rules
under the same flag key. Evaluation deliberately supplies only the authenticated
organization, so the selector rollout is organization-wide, not user-specific.
An organization needs a Helm policy entry as well as an enabled flag to see it.
If flag evaluation fails, the selector is disabled while the organization's
allowlist and ordinary default launches remain available.

The policy uses the request's authenticated effective organization, including the
existing validated `X-Org-Id` and API-key binding rules. Options and new-sandbox
creation are filtered server-side. A configured policy remains enforced even
when the selector is hidden. Organizations without a policy retain the legacy
behavior; an enabled deployment flag alone does not display a selector for them.

**Default** omits the override. An allowed saved user default is used; otherwise
the organization default is used. It does not replace the user's saved setting.
Keep the organization default defined in runtime-api; removing it makes default
launches fail closed rather than selecting another organization's template.
Changing organizations clears the pending selection and uses a separate options
cache. Local backends and older servers without the options endpoint keep their
existing home-page UI.

## API compatibility and stored state

`GET /api/v1/sandbox-specs/selection` returns `enabled` and a list of `id` /
`display_name` options, without template environment values or credentials.

`POST /api/v1/app-conversations` accepts one new optional field,
`sandbox_spec_id`. Existing callers omit it and continue to work. The field
cannot be combined with `sandbox_id` or `parent_conversation_id` (children inherit
their parent's sandbox); explicit choices must be enabled and
allowed for the effective organization. No image or resource overrides are
accepted through this field.

The request's template ID is already covered by the start task's JSON request.
The created sandbox stores its template ID, and runtime-api stores the resolved
resource requirements. There are no new database columns and no conversation
backfill or migration. Existing sandbox metadata remains readable for archival
and existing runtime pause/resume keeps its saved sizing.

An explicit template always creates a new sandbox. Configured organization
policies also disable automatic reuse of user-owned grouped sandboxes, because
those records do not carry an organization identity. This avoids reusing a
sandbox from another organization. Normal concurrency limits still apply.

## Delivery

Deploy the runtime-api template support and Enterprise API support before the
updated Canvas frontend, then enable the flag for selected organizations.
Enterprise's separately pinned/built Canvas bundle must consume the selector
frontend version; upgrading the Enterprise backend alone does not add the button.

This is a Helm configuration feature. Replicated Admin Console template editing
and additional capacity admission controls remain outside this change.
