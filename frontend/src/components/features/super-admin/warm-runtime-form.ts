import type {
  WarmRuntime,
  WarmRuntimeFields,
  WarmRuntimeResourceValues,
  WarmRuntimeResources,
} from "#/api/warm-runtimes-service/warm-runtimes.types";

export const WARM_RUNTIME_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/;

const DEFAULT_WORKING_DIR = "/home/openhands/workspace";

export interface WarmRuntimeFormState {
  name: string;
  image: string;
  workingDir: string;
  /** One entry per line. */
  command: string;
  /** One entry per line. */
  args: string;
  /** `KEY=value`, one per line. */
  environment: string;
  cpuRequest: string;
  cpuLimit: string;
  memoryRequest: string;
  memoryLimit: string;
  ephemeralStorageRequest: string;
  ephemeralStorageLimit: string;
  idleSeconds: string;
  count: string;
  persistentStorageSize: string;
  runAsUser: string;
  runAsGroup: string;
  fsGroup: string;
  fuseS3Mount: boolean;
}

export type WarmRuntimeFormErrorKey =
  | "name"
  | "image"
  | "workingDir"
  | "environment"
  | "idleSeconds"
  | "count"
  | "runAsUser"
  | "runAsGroup"
  | "fsGroup";

export type WarmRuntimeFormErrors = Partial<
  Record<WarmRuntimeFormErrorKey, "required" | "invalid" | "integer">
> & { environmentLine?: number };

export const EMPTY_WARM_RUNTIME_FORM: WarmRuntimeFormState = {
  name: "",
  image: "",
  workingDir: DEFAULT_WORKING_DIR,
  command: "",
  args: "",
  environment: "",
  cpuRequest: "",
  cpuLimit: "",
  memoryRequest: "",
  memoryLimit: "",
  ephemeralStorageRequest: "",
  ephemeralStorageLimit: "",
  idleSeconds: "",
  count: "",
  persistentStorageSize: "",
  runAsUser: "",
  runAsGroup: "",
  fsGroup: "",
  fuseS3Mount: false,
};

const fromLines = (text: string) =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

const toNumberString = (value: number | null | undefined) =>
  value === null || value === undefined ? "" : String(value);

const toEnvironmentText = (environment: Record<string, string>) =>
  Object.entries(environment)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");

export const toFormState = (runtime: WarmRuntime): WarmRuntimeFormState => ({
  name: runtime.name,
  image: runtime.image,
  workingDir: runtime.working_dir,
  command: runtime.command.join("\n"),
  args: runtime.args.join("\n"),
  environment: toEnvironmentText(runtime.environment),
  cpuRequest: runtime.resources?.requests?.cpu ?? "",
  cpuLimit: runtime.resources?.limits?.cpu ?? "",
  memoryRequest: runtime.resources?.requests?.memory ?? "",
  memoryLimit: runtime.resources?.limits?.memory ?? "",
  ephemeralStorageRequest: runtime.resources?.requests?.ephemeral_storage ?? "",
  ephemeralStorageLimit: runtime.resources?.limits?.ephemeral_storage ?? "",
  idleSeconds: toNumberString(runtime.idle_seconds),
  count: toNumberString(runtime.count),
  persistentStorageSize: runtime.persistent_storage_size ?? "",
  runAsUser: toNumberString(runtime.run_as_user),
  runAsGroup: toNumberString(runtime.run_as_group),
  fsGroup: toNumberString(runtime.fs_group),
  fuseS3Mount: runtime.fuse_s3_mount ?? false,
});

/** Returns the 1-based number of the first line that is not `KEY=value`. */
const findInvalidEnvironmentLine = (text: string) => {
  const lines = text.split("\n");
  const index = lines.findIndex((line) => {
    const trimmed = line.trim();
    return trimmed !== "" && trimmed.indexOf("=") < 1;
  });
  return index === -1 ? null : index + 1;
};

const parseEnvironment = (text: string) =>
  Object.fromEntries(
    fromLines(text).map((line) => {
      const separator = line.indexOf("=");
      return [line.slice(0, separator).trim(), line.slice(separator + 1)];
    }),
  );

const isNonNegativeInteger = (value: string) => /^\d+$/.test(value.trim());

const integerError = (value: string, minimum: number) => {
  if (!value.trim()) {
    return undefined;
  }
  return isNonNegativeInteger(value) && Number(value) >= minimum
    ? undefined
    : ("integer" as const);
};

export const validateWarmRuntimeForm = (
  form: WarmRuntimeFormState,
  { isNew }: { isNew: boolean },
): WarmRuntimeFormErrors => {
  const errors: WarmRuntimeFormErrors = {};
  if (isNew) {
    if (!form.name.trim()) {
      errors.name = "required";
    } else if (!WARM_RUNTIME_NAME_PATTERN.test(form.name.trim())) {
      errors.name = "invalid";
    }
  }
  if (!form.image.trim()) {
    errors.image = "required";
  }
  if (!form.workingDir.trim()) {
    errors.workingDir = "required";
  }
  const environmentLine = findInvalidEnvironmentLine(form.environment);
  if (environmentLine !== null) {
    errors.environment = "invalid";
    errors.environmentLine = environmentLine;
  }
  errors.idleSeconds = integerError(form.idleSeconds, 1);
  errors.count = integerError(form.count, 0);
  errors.runAsUser = integerError(form.runAsUser, 0);
  errors.runAsGroup = integerError(form.runAsGroup, 0);
  errors.fsGroup = integerError(form.fsGroup, 0);
  return Object.fromEntries(
    Object.entries(errors).filter(([, value]) => value !== undefined),
  );
};

const resourceValues = (
  cpu: string,
  memory: string,
  ephemeralStorage: string,
): WarmRuntimeResourceValues | null => {
  const values: WarmRuntimeResourceValues = {};
  if (cpu.trim()) values.cpu = cpu.trim();
  if (memory.trim()) values.memory = memory.trim();
  if (ephemeralStorage.trim()) {
    values.ephemeral_storage = ephemeralStorage.trim();
  }
  return Object.keys(values).length > 0 ? values : null;
};

const buildResources = (
  form: WarmRuntimeFormState,
): WarmRuntimeResources | null => {
  const requests = resourceValues(
    form.cpuRequest,
    form.memoryRequest,
    form.ephemeralStorageRequest,
  );
  const limits = resourceValues(
    form.cpuLimit,
    form.memoryLimit,
    form.ephemeralStorageLimit,
  );
  if (!requests && !limits) {
    return null;
  }
  return { requests, limits };
};

const toOptionalNumber = (value: string) =>
  value.trim() ? Number(value) : null;

/** Assumes `validateWarmRuntimeForm` returned no errors. */
export const buildWarmRuntimePayload = (
  form: WarmRuntimeFormState,
): WarmRuntimeFields => ({
  image: form.image.trim(),
  working_dir: form.workingDir.trim(),
  command: fromLines(form.command),
  args: fromLines(form.args),
  environment: parseEnvironment(form.environment),
  resources: buildResources(form),
  idle_seconds: toOptionalNumber(form.idleSeconds),
  persistent_storage_size: form.persistentStorageSize.trim() || null,
  count: toOptionalNumber(form.count),
  run_as_user: toOptionalNumber(form.runAsUser),
  run_as_group: toOptionalNumber(form.runAsGroup),
  fs_group: toOptionalNumber(form.fsGroup),
  fuse_s3_mount: form.fuseS3Mount || null,
});

/** "2 · 4Gi" (CPU · memory) from a runtime's limits, falling back to requests. */
export const summarizeResources = (runtime: WarmRuntime) => {
  const { requests, limits } = runtime.resources ?? {};
  const cpu = limits?.cpu ?? requests?.cpu;
  const memory = limits?.memory ?? requests?.memory;
  return [cpu, memory].filter(Boolean).join(" · ");
};
