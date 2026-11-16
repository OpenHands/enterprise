import { create } from "zustand";
import { devtools } from "zustand/middleware";

interface InvitationEmailMismatchState {
  /** An invitation was refused because the account's email differs. */
  isOpen: boolean;
}

interface InvitationEmailMismatchActions {
  open: () => void;
  close: () => void;
}

type InvitationEmailMismatchStore = InvitationEmailMismatchState &
  InvitationEmailMismatchActions;

const initialState: InvitationEmailMismatchState = {
  isOpen: false,
};

export const useInvitationEmailMismatchStore =
  create<InvitationEmailMismatchStore>()(
    devtools(
      (set) => ({
        ...initialState,
        open: () => set({ isOpen: true }),
        close: () => set({ isOpen: false }),
      }),
      { name: "InvitationEmailMismatchStore" },
    ),
  );
