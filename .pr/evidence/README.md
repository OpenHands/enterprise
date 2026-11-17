# Synthetic local UI evidence

These images show disposable local users and organizations. No customer data or infrastructure identifiers are included. Screenshot pixels and PNG metadata were inspected.

- `organization-default-profile.png`: owner controls for the organization default.
- `profile-sandbox-image.png`: optional image on the shared profile.
- `inherited-profile-image.png`: member's saved image is empty while the effective profile image is displayed as inherited.

Agent-operated browser checks exercised owner default set/clear, image save/restore, member image override/clear, member profile selection/deactivation, and read-only organization controls. All ten checks passed.

The frontend sources match product commit `1da7e782366540e20bd9c27b57e095c9e6eaafa4`. A subsequent backend-only guard rejects legacy organization ACP defaults; its HTTP regression failed before the guard and passed afterward. The final committed image was separately exercised through the native acceptance suite. Parent-profile inheritance, copy-profile UI, and per-automation image selection are excluded.
