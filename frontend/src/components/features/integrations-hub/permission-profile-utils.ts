import type {
  HubIntegration,
  HubPermissionProfile,
  HubPermissionProfileSnapshot,
  HubToolAccessMode,
} from "#/types/integrations-hub";

export interface PermissionProfileEditorTool {
  name: string;
  description: string;
  accessMode: HubToolAccessMode;
  missing: boolean;
}

export interface PermissionProfileEditorIntegration {
  key: string;
  name: string;
  enabled: boolean;
  installed: boolean;
  tools: PermissionProfileEditorTool[];
}

export function emptyPermissionProfileSnapshot(): HubPermissionProfileSnapshot {
  return { integrations: {} };
}

export function snapshotFromIntegrations(
  integrations: HubIntegration[],
): HubPermissionProfileSnapshot {
  return {
    integrations: Object.fromEntries(
      integrations.map((item) => [
        item.slug,
        {
          enabled: item.enabled,
          tools: Object.fromEntries(
            item.tools.map((tool) => [tool.name, tool.accessMode]),
          ),
        },
      ]),
    ),
  };
}

export function formatPermissionProfileSummary(
  snapshot: HubPermissionProfileSnapshot,
  integrations: HubIntegration[],
): string {
  const names = integrations
    .filter((item) => snapshot.integrations[item.slug]?.enabled)
    .map((item) => item.name);
  const enabledTools = Object.values(snapshot.integrations).reduce(
    (count, entry) => {
      if (!entry.enabled) {
        return count;
      }
      return (
        count +
        Object.values(entry.tools).filter((mode) => mode !== "disabled").length
      );
    },
    0,
  );
  if (names.length === 0) {
    return `${enabledTools} tools enabled`;
  }
  return `${names.join(", ")} · ${enabledTools} tools enabled`;
}

export function buildPermissionProfileEditorRows(
  snapshot: HubPermissionProfileSnapshot,
  integrations: HubIntegration[],
): PermissionProfileEditorIntegration[] {
  const installedByKey = new Map(
    integrations.map((integration) => [integration.slug, integration]),
  );
  const keys = new Set([
    ...integrations.map((integration) => integration.slug),
    ...Object.keys(snapshot.integrations),
  ]);

  const rows: PermissionProfileEditorIntegration[] = [];
  for (const key of keys) {
    const installed = installedByKey.get(key);
    const saved = snapshot.integrations[key];
    const toolNames = new Set([
      ...(installed?.tools.map((tool) => tool.name) ?? []),
      ...Object.keys(saved?.tools ?? {}),
    ]);

    const tools: PermissionProfileEditorTool[] = [];
    for (const toolName of toolNames) {
      const spec = installed?.tools.find((tool) => tool.name === toolName);
      tools.push({
        name: toolName,
        description: spec?.description ?? "",
        accessMode: saved?.tools[toolName] ?? "disabled",
        missing: !spec,
      });
    }
    tools.sort((left, right) => left.name.localeCompare(right.name));

    rows.push({
      key,
      name: installed?.name ?? key,
      enabled: saved?.enabled ?? false,
      installed: Boolean(installed),
      tools,
    });
  }

  rows.sort((left, right) => left.name.localeCompare(right.name));
  return rows;
}

export function editorRowsToSnapshot(
  rows: PermissionProfileEditorIntegration[],
): HubPermissionProfileSnapshot {
  return {
    integrations: Object.fromEntries(
      rows.map((row) => [
        row.key,
        {
          enabled: row.enabled,
          tools: Object.fromEntries(
            row.tools.map((tool) => [tool.name, tool.accessMode]),
          ),
        },
      ]),
    ),
  };
}

const stableSnapshotPayload = (snapshot: HubPermissionProfileSnapshot) =>
  Object.keys(snapshot.integrations)
    .sort()
    .map((key) => {
      const entry = snapshot.integrations[key];
      if (!entry) {
        return [key, false, []] as const;
      }
      return [
        key,
        entry.enabled,
        Object.keys(entry.tools)
          .sort()
          .map((toolName) => [toolName, entry.tools[toolName]]),
      ] as const;
    });

export function permissionProfileSnapshotsEqual(
  left: HubPermissionProfileSnapshot,
  right: HubPermissionProfileSnapshot,
) {
  return (
    JSON.stringify(stableSnapshotPayload(left)) ===
    JSON.stringify(stableSnapshotPayload(right))
  );
}

function matchesQuery(values: string[], query: string) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  return values.some((value) => value.toLowerCase().includes(normalized));
}

export function matchesPermissionProfileEditorSearch(
  row: PermissionProfileEditorIntegration,
  query: string,
) {
  return matchesQuery(
    [row.name, row.key, ...row.tools.map((tool) => tool.name)],
    query,
  );
}

export function matchesPermissionProfileToolSearch(
  tool: PermissionProfileEditorTool,
  query: string,
) {
  return matchesQuery([tool.name, tool.description], query);
}

export function profileSnapshotOrEmpty(
  profile: HubPermissionProfile,
): HubPermissionProfileSnapshot {
  return profile.snapshot ?? emptyPermissionProfileSnapshot();
}
