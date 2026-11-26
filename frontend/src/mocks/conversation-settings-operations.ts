import { ConversationSettingOperation, SettingsValue } from "#/types/settings";

export function applyConversationSettingOperations(
  settings: Record<string, SettingsValue>,
  operations: ConversationSettingOperation[],
  defaults: Record<string, SettingsValue> = {},
): Record<string, SettingsValue> {
  const next = { ...settings };
  for (const operation of operations) {
    const [, key] = operation.path;
    if (operation.op === "set") next[key] = operation.value;
    else delete next[key];
  }
  return { ...defaults, ...next };
}
