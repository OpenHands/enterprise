import type {
  SettingsFieldSchema,
  SettingsSchema,
  SettingsValue,
} from "#/types/settings";
import {
  coerceFieldValue,
  isSettingsFieldVisible,
  normalizeFieldValue,
  type SettingsFormValues,
} from "#/utils/sdk-settings-schema";

/** Agent-settings sections an OpenHands agent profile stores as its own. */
export type ProfileBehaviorSection = "condenser" | "verification";

export const PROFILE_BEHAVIOR_SECTIONS: readonly ProfileBehaviorSection[] = [
  "condenser",
  "verification",
];

/**
 * Fields of those sections that stay global (same list as Agent Canvas):
 * every agent shares the critic API key, and the confirmation controls
 * belong to conversation settings. The SDK profile model has no such
 * fields either.
 */
export const GLOBAL_BEHAVIOR_FIELD_KEYS = new Set([
  "verification.critic_api_key",
  "verification.confirmation_mode",
  "verification.security_analyzer",
]);

/** The schema fields of ``section`` a profile stores. */
export function profileBehaviorFields(
  schema: SettingsSchema | null | undefined,
  section: ProfileBehaviorSection,
): SettingsFieldSchema[] {
  return (
    schema?.sections
      .find((candidate) => candidate.key === section)
      ?.fields.filter((field) => !GLOBAL_BEHAVIOR_FIELD_KEYS.has(field.key)) ??
    []
  );
}

/** ``condenser.max_size`` -> ``max_size``: the key inside the stored object. */
function storedKey(section: ProfileBehaviorSection, fieldKey: string): string {
  const prefix = `${section}.`;
  return fieldKey.startsWith(prefix) ? fieldKey.slice(prefix.length) : fieldKey;
}

/** A stored profile section as form values; fields it lacks take the schema default. */
export function toBehaviorFormValues(
  schema: SettingsSchema | null | undefined,
  section: ProfileBehaviorSection,
  stored: Record<string, unknown> | null | undefined,
): SettingsFormValues {
  return Object.fromEntries(
    profileBehaviorFields(schema, section).map((field) => [
      field.key,
      normalizeFieldValue(field, stored?.[storedKey(section, field.key)]),
    ]),
  );
}

/**
 * Form values as the profile section to store. Fields hidden by their
 * ``depends_on`` and values cleared to empty are left out, so the stored
 * section keeps the server defaults for them. Throws on a value that fails
 * coercion.
 */
export function fromBehaviorFormValues(
  schema: SettingsSchema | null | undefined,
  section: ProfileBehaviorSection,
  values: SettingsFormValues,
): Record<string, SettingsValue> {
  return Object.fromEntries(
    profileBehaviorFields(schema, section)
      .filter(
        (field) => field.key in values && isSettingsFieldVisible(field, values),
      )
      .map((field): [string, SettingsValue] => [
        storedKey(section, field.key),
        coerceFieldValue(field, values[field.key]),
      ])
      .filter(([, value]) => value !== null),
  );
}
