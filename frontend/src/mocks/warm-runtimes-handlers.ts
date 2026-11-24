import { http, HttpResponse } from "msw";
import type {
  CreateWarmRuntimeParams,
  WarmRuntime,
  WarmRuntimeFields,
} from "#/api/warm-runtimes-service/warm-runtimes.types";

let warmRuntimes: WarmRuntime[] = [
  {
    name: "v1_current",
    image: "ghcr.io/openhands/agent-server:latest",
    working_dir: "/home/openhands/workspace",
    command: [],
    args: [],
    environment: {},
    resources: {
      requests: { cpu: "500m", memory: "2Gi" },
      limits: { cpu: "2", memory: "4Gi" },
    },
    idle_seconds: 1800,
    persistent_storage_size: null,
    count: 1,
    run_as_user: null,
    run_as_group: null,
    fs_group: null,
    fuse_s3_mount: null,
    source: "file",
  },
];

export const WARM_RUNTIMES_HANDLERS = [
  http.get("/api/admin/warm-runtimes", () =>
    HttpResponse.json({ configs: warmRuntimes }),
  ),

  http.post("/api/admin/warm-runtimes", async ({ request }) => {
    const body = (await request.json()) as CreateWarmRuntimeParams;
    if (warmRuntimes.some((runtime) => runtime.name === body.name)) {
      return HttpResponse.json(
        { detail: `Warm runtime "${body.name}" already exists` },
        { status: 409 },
      );
    }
    const created: WarmRuntime = { ...body, source: "db" };
    warmRuntimes = [...warmRuntimes, created];
    return HttpResponse.json(created, { status: 201 });
  }),

  http.put("/api/admin/warm-runtimes/:name", async ({ params, request }) => {
    const body = (await request.json()) as WarmRuntimeFields;
    const name = String(params.name);
    if (!warmRuntimes.some((runtime) => runtime.name === name)) {
      return HttpResponse.json(
        { detail: `Warm runtime "${name}" not found` },
        { status: 404 },
      );
    }
    const updated: WarmRuntime = { ...body, name, source: "db" };
    warmRuntimes = warmRuntimes.map((runtime) =>
      runtime.name === name ? updated : runtime,
    );
    return HttpResponse.json(updated);
  }),

  http.delete("/api/admin/warm-runtimes/:name", ({ params }) => {
    const name = String(params.name);
    warmRuntimes = warmRuntimes.filter((runtime) => runtime.name !== name);
    return new HttpResponse(null, { status: 204 });
  }),
];
