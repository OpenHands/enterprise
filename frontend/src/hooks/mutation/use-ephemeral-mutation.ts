import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { safeNativeError } from "#/api/native-auth-service/native-auth-service.api";

export interface EphemeralMutation<Input, Output> {
  run: (input: Input) => Promise<Output>;
  isPending: boolean;
  reset: () => void;
}
export type EphemeralMutationFor<
  Operation extends (input: never) => Promise<unknown>,
> = EphemeralMutation<Parameters<Operation>[0], Awaited<ReturnType<Operation>>>;

/** Passwords and one-time links live only in the active operation and caller.
 * TanStack receives neither input variables nor the result, even while pending.
 */
export function useEphemeralMutation<Input, Output>(
  operation: (input: Input) => Promise<Output>,
): EphemeralMutation<Input, Output> {
  const job = useRef<{
    input: Input;
    resolve: (output: Output) => void;
    reject: (error: Error) => void;
  } | null>(null);
  const busy = useRef(false);
  // Strict Mode can detach the mutation observer after an effect starts a job.
  // Pending follows the serialized operation, whose promise still completes.
  const [isPending, setIsPending] = useState(false);
  const mutation = useMutation<void, Error, void>({
    mutationFn: async () => {
      const { current } = job;
      job.current = null;
      if (!current) return;
      try {
        current.resolve(await operation(current.input));
      } catch (error) {
        const safe = safeNativeError(error);
        current.reject(safe);
        throw safe;
      } finally {
        busy.current = false;
        setIsPending(false);
      }
    },
    retry: false,
    // Never retain credentials in a paused offline mutation or replay them later.
    networkMode: "always",
    gcTime: 0,
    meta: { disableToast: true, skipAuthInvalidation: true },
  });
  const run = (input: Input): Promise<Output> => {
    if (busy.current)
      return Promise.reject(new Error("A request is already in progress."));
    busy.current = true;
    setIsPending(true);
    return new Promise((resolve, reject) => {
      job.current = { input, resolve, reject };
      mutation.mutate();
    });
  };
  return { run, isPending, reset: mutation.reset };
}
