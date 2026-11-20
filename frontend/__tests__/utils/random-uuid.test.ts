import { afterEach, describe, expect, it, vi } from "vitest";
import { randomUUID } from "#/utils/random-uuid";
import { useBtwStore } from "#/stores/btw-store";
import { useModelStore } from "#/stores/model-store";

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

// Plain-HTTP non-loopback origins are not secure contexts: browsers expose
// crypto.getRandomValues there but leave crypto.randomUUID undefined.
function stubNonSecureContextCrypto() {
  const realCrypto = globalThis.crypto;
  const getRandomValues = vi.fn(<T extends ArrayBufferView>(array: T) =>
    realCrypto.getRandomValues(array),
  );
  vi.stubGlobal("crypto", { getRandomValues });
  return getRandomValues;
}

afterEach(() => {
  vi.unstubAllGlobals();
  useBtwStore.setState({ entriesByConversation: {} });
  useModelStore.setState({
    entriesByConversation: {},
    activeProfileByConversation: {},
  });
});

describe("randomUUID outside a secure context", () => {
  it("returns distinct UUIDv4 values built from getRandomValues", () => {
    const getRandomValues = stubNonSecureContextCrypto();
    expect(globalThis.crypto.randomUUID).toBeUndefined();

    const ids = Array.from({ length: 50 }, () => randomUUID());

    ids.forEach((id) => expect(id).toMatch(UUID_V4));
    expect(new Set(ids).size).toBe(ids.length);
    expect(getRandomValues).toHaveBeenCalledTimes(ids.length);
  });

  it("sets the version and variant bits regardless of the random bytes", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (array: Uint8Array) => array.fill(0xff),
    });
    expect(randomUUID()).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");

    vi.stubGlobal("crypto", {
      getRandomValues: (array: Uint8Array) => array.fill(0),
    });
    expect(randomUUID()).toBe("00000000-0000-4000-8000-000000000000");
  });

  it("lets /btw add its pending entry", () => {
    stubNonSecureContextCrypto();

    const id = useBtwStore.getState().addPending("conv", "why?");

    expect(id).toMatch(UUID_V4);
    expect(useBtwStore.getState().entriesByConversation.conv).toEqual([
      { id, question: "why?", status: "pending" },
    ]);
  });

  it("lets /model add its list and switch entries", () => {
    stubNonSecureContextCrypto();

    useModelStore.getState().show("conv", null, []);
    useModelStore.getState().recordSwitch("conv", "evt-1", "fast");

    const entries = useModelStore.getState().entriesByConversation.conv;
    expect(entries).toHaveLength(2);
    entries.forEach((entry) => expect(entry.id).toMatch(UUID_V4));
    expect(entries[1]).toMatchObject({ switchedTo: "fast" });
  });
});
