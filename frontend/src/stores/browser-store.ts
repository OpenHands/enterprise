import { create } from "zustand";

interface BrowserState {
  // Placeholder until replaced by the actual URL
  url: string;
  // Base64-encoded screenshot; placeholder until replaced by the actual screenshot
  screenshotSrc: string;
}

interface BrowserStore extends BrowserState {
  setUrl: (url: string) => void;
  setScreenshotSrc: (screenshotSrc: string) => void;
  reset: () => void;
}

const initialState: BrowserState = {
  url: "https://github.com/OpenHands/OpenHands",
  screenshotSrc: "",
};

export const useBrowserStore = create<BrowserStore>((set) => ({
  ...initialState,
  setUrl: (url: string) => set({ url }),
  setScreenshotSrc: (screenshotSrc: string) => set({ screenshotSrc }),
  reset: () => set(initialState),
}));
