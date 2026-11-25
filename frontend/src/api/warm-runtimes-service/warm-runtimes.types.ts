export interface WarmRuntimeResourceValues {
  cpu?: string | null;
  memory?: string | null;
  ephemeral_storage?: string | null;
}

export interface WarmRuntimeResources {
  requests?: WarmRuntimeResourceValues | null;
  limits?: WarmRuntimeResourceValues | null;
}

/** The settings a super admin can change; everything but `name`. */
export interface WarmRuntimeFields {
  image: string;
  working_dir: string;
  command: string[];
  args: string[];
  environment: Record<string, string>;
  resources: WarmRuntimeResources | null;
  idle_seconds: number | null;
  persistent_storage_size: string | null;
  count: number | null;
  run_as_user: number | null;
  run_as_group: number | null;
  fs_group: number | null;
  fuse_s3_mount: boolean | null;
}

/** `db` was saved through runtime-api's API; `file` comes from its ConfigMap. */
export type WarmRuntimeSource = "db" | "file";

export interface WarmRuntime extends WarmRuntimeFields {
  name: string;
  source: WarmRuntimeSource | null;
  /** A private runtime is usable only by the organizations granted access. */
  is_private: boolean;
}

export interface WarmRuntimeAccessOrg {
  id: string;
  name: string;
}

export interface WarmRuntimeAccess {
  is_private: boolean;
  orgs: WarmRuntimeAccessOrg[];
}

export interface UpdateWarmRuntimeAccessParams {
  name: string;
  is_private: boolean;
  org_ids: string[];
}

export interface WarmRuntimeList {
  configs: WarmRuntime[];
}

export type CreateWarmRuntimeParams = WarmRuntimeFields & { name: string };
