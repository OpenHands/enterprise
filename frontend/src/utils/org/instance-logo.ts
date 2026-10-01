import { useSyncExternalStore } from "react";

/** Instance mark managed on Super Admin → Instance. Shown beside the product logo. */
export const INSTANCE_LOGO_STORAGE_KEY = "oh-instance-logo";

const listeners = new Set<() => void>();

function parseStored(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(INSTANCE_LOGO_STORAGE_KEY);
    return raw && raw.startsWith("data:image/") ? raw : null;
  } catch {
    return null;
  }
}

let snapshot = parseStored();

function notify() {
  snapshot = parseStored();
  listeners.forEach((listener) => listener());
}

export function readInstanceLogo(): string | null {
  return snapshot;
}

export function setInstanceLogo(logoDataUrl: string | null) {
  if (typeof window === "undefined") {
    return;
  }
  if (logoDataUrl) {
    window.localStorage.setItem(INSTANCE_LOGO_STORAGE_KEY, logoDataUrl);
  } else {
    window.localStorage.removeItem(INSTANCE_LOGO_STORAGE_KEY);
  }
  notify();
}

export function subscribeInstanceLogo(onStoreChange: () => void) {
  listeners.add(onStoreChange);
  const onStorage = (event: StorageEvent) => {
    if (event.key === INSTANCE_LOGO_STORAGE_KEY) {
      notify();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onStoreChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function useInstanceLogo(): string | null {
  return useSyncExternalStore(
    subscribeInstanceLogo,
    readInstanceLogo,
    () => null,
  );
}

/** Shrink an uploaded image so the instance setting stays a small data URL. */
export function readImageFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const maxEdge = 256;
      const scale = Math.min(1, maxEdge / Math.max(image.width, image.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.width * scale));
      canvas.height = Math.max(1, Math.round(image.height * scale));
      const context = canvas.getContext("2d");
      if (!context) {
        URL.revokeObjectURL(objectUrl);
        reject(new Error("Could not read the image."));
        return;
      }
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(objectUrl);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Could not read the image."));
    };
    image.src = objectUrl;
  });
}
