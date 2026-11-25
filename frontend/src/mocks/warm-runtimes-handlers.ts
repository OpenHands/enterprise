import { http, HttpResponse } from "msw";
import type {
  CreateWarmRuntimeParams,
  UpdateWarmRuntimeAccessParams,
  WarmRuntime,
  WarmRuntimeAccess,
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
    is_private: false,
  },
];

let accessOrgs: WarmRuntimeAccess["orgs"] = [];

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
    const created: WarmRuntime = { ...body, source: "db", is_private: false };
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
    const current = warmRuntimes.find((runtime) => runtime.name === name)!;
    const updated: WarmRuntime = {
      ...body,
      name,
      source: "db",
      is_private: current.is_private,
    };
    warmRuntimes = warmRuntimes.map((runtime) =>
      runtime.name === name ? updated : runtime,
    );
    return HttpResponse.json(updated);
  }),

  http.get("/api/admin/warm-runtimes/:name/access", ({ params }) => {
    const runtime = warmRuntimes.find((item) => item.name === params.name);
    if (!runtime) {
      return HttpResponse.json({ detail: "not found" }, { status: 404 });
    }
    return HttpResponse.json<WarmRuntimeAccess>({
      is_private: runtime.is_private,
      orgs: runtime.is_private ? accessOrgs : [],
    });
  }),

  http.put(
    "/api/admin/warm-runtimes/:name/access",
    async ({ params, request }) => {
      const body = (await request.json()) as Omit<
        UpdateWarmRuntimeAccessParams,
        "name"
      >;
      const name = String(params.name);
      if (!warmRuntimes.some((runtime) => runtime.name === name)) {
        return HttpResponse.json({ detail: "not found" }, { status: 404 });
      }
      warmRuntimes = warmRuntimes.map((runtime) =>
        runtime.name === name
          ? { ...runtime, is_private: body.is_private }
          : runtime,
      );
      accessOrgs = body.is_private
        ? body.org_ids.map((id) => ({ id, name: id }))
        : [];
      return HttpResponse.json<WarmRuntimeAccess>({
        is_private: body.is_private,
        orgs: accessOrgs,
      });
    },
  ),

  http.delete("/api/admin/warm-runtimes/:name", ({ params }) => {
    const name = String(params.name);
    warmRuntimes = warmRuntimes.filter((runtime) => runtime.name !== name);
    return new HttpResponse(null, { status: 204 });
  }),
];
