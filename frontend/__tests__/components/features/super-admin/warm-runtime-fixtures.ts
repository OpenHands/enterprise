import type { WarmRuntime } from "#/api/warm-runtimes-service/warm-runtimes.types";

export const CONFIGMAP_RUNTIME: WarmRuntime = {
  name: "v1_current",
  image: "ghcr.io/openhands/agent-server:1",
  working_dir: "/workspace",
  command: [],
  args: [],
  environment: {},
  resources: null,
  idle_seconds: null,
  persistent_storage_size: null,
  count: 1,
  run_as_user: null,
  run_as_group: null,
  fs_group: null,
  fuse_s3_mount: null,
  source: "file",
  is_private: false,
};

export const LARGE_RUNTIME: WarmRuntime = {
  ...CONFIGMAP_RUNTIME,
  name: "large",
  image: "registry.example/custom:2",
  command: ["agent-server", "--verbose"],
  environment: { HEADER: "X-Key=${SESSION_API_KEY}" },
  resources: {
    requests: { cpu: "1", memory: "2Gi" },
    limits: { cpu: "2", memory: "4Gi", ephemeral_storage: "20Gi" },
  },
  idle_seconds: 900,
  run_as_user: 10001,
  source: "db",
  is_private: true,
};
