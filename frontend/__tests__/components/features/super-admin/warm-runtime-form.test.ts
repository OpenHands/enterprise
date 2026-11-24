import { describe, expect, it } from "vitest";
import {
  EMPTY_WARM_RUNTIME_FORM,
  buildWarmRuntimePayload,
  summarizeResources,
  toFormState,
  validateWarmRuntimeForm,
} from "#/components/features/super-admin/warm-runtime-form";
import { CONFIGMAP_RUNTIME, LARGE_RUNTIME } from "./warm-runtime-fixtures";

const valid = { ...EMPTY_WARM_RUNTIME_FORM, name: "big", image: "img:1" };

describe("warm runtime form", () => {
  it("round-trips an existing runtime through the form", () => {
    const { name, source, ...fields } = LARGE_RUNTIME;

    expect(buildWarmRuntimePayload(toFormState(LARGE_RUNTIME))).toEqual(fields);
    expect([name, source]).toEqual(["large", "db"]);
  });

  it("omits empty optional values from the payload", () => {
    expect(buildWarmRuntimePayload(valid)).toEqual({
      image: "img:1",
      working_dir: "/home/openhands/workspace",
      command: [],
      args: [],
      environment: {},
      resources: null,
      idle_seconds: null,
      persistent_storage_size: null,
      count: null,
      run_as_user: null,
      run_as_group: null,
      fs_group: null,
      fuse_s3_mount: null,
    });
  });

  it("splits environment lines on the first equals sign and ignores blank lines", () => {
    const payload = buildWarmRuntimePayload({
      ...valid,
      environment: "A=1\n\n  B = x=y \nC=",
    });

    expect(payload.environment).toEqual({ A: "1", B: " x=y", C: "" });
  });

  it("sends only the resource sections that have values", () => {
    const payload = buildWarmRuntimePayload({ ...valid, memoryLimit: " 4Gi " });

    expect(payload.resources).toEqual({
      requests: null,
      limits: { memory: "4Gi" },
    });
  });

  it("accepts a complete new runtime", () => {
    expect(validateWarmRuntimeForm(valid, { isNew: true })).toEqual({});
  });

  it.each([
    [{ name: "" }, "name", "required"],
    [{ name: "bad name" }, "name", "invalid"],
    [{ name: "-lead" }, "name", "invalid"],
    [{ image: " " }, "image", "required"],
    [{ workingDir: "" }, "workingDir", "required"],
    [{ idleSeconds: "0" }, "idleSeconds", "integer"],
    [{ idleSeconds: "1.5" }, "idleSeconds", "integer"],
    [{ count: "-1" }, "count", "integer"],
    [{ runAsUser: "abc" }, "runAsUser", "integer"],
  ])("rejects %j", (override, field, kind) => {
    const errors = validateWarmRuntimeForm(
      { ...valid, ...override },
      { isNew: true },
    );

    expect(errors[field as keyof typeof errors]).toBe(kind);
  });

  it("accepts zero warm instances", () => {
    expect(
      validateWarmRuntimeForm({ ...valid, count: "0" }, { isNew: true }),
    ).toEqual({});
  });

  it("does not validate the name of an existing runtime", () => {
    expect(
      validateWarmRuntimeForm({ ...valid, name: "" }, { isNew: false }),
    ).toEqual({});
  });

  it("points at the first malformed environment line", () => {
    const errors = validateWarmRuntimeForm(
      { ...valid, environment: "A=1\n\nnot-a-pair\n=x" },
      { isNew: true },
    );

    expect(errors).toEqual({ environment: "invalid", environmentLine: 3 });
  });

  it("summarizes limits, falling back to requests", () => {
    expect(summarizeResources(LARGE_RUNTIME)).toBe("2 · 4Gi");
    expect(
      summarizeResources({
        ...LARGE_RUNTIME,
        resources: { requests: { cpu: "1" }, limits: { memory: "4Gi" } },
      }),
    ).toBe("1 · 4Gi");
    expect(summarizeResources(CONFIGMAP_RUNTIME)).toBe("");
  });
});
