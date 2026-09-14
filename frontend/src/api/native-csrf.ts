import axios, {
  AxiosInstance,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";

type CsrfRequest = InternalAxiosRequestConfig<unknown> & {
  nativeCsrfRetried: boolean;
};

/** Browser proof belongs to the selected OpenHands session transport. */
export class NativeCsrf {
  private proof: string | undefined;

  private generation = 0;

  private pendingProof: Promise<string> | undefined;

  reset(): void {
    this.generation += 1;
    this.proof = undefined;
    this.pendingProof = undefined;
  }

  private getProof(client: AxiosInstance): Promise<string> {
    if (this.proof) return Promise.resolve(this.proof);
    if (this.pendingProof) return this.pendingProof;
    const currentGeneration = this.generation;
    this.pendingProof = client
      .get<{ csrf_token: string }>("/api/auth/csrf", { withCredentials: true })
      .then(
        ({
          data,
        }: AxiosResponse<{ csrf_token: string }, unknown>):
          | string
          | Promise<string> => {
          if (this.generation !== currentGeneration)
            return this.getProof(client);
          this.proof = data.csrf_token;
          return this.proof;
        },
      )
      .finally((): void => {
        if (this.generation === currentGeneration)
          this.pendingProof = undefined;
      });
    return this.pendingProof;
  }

  async prepareMutation(
    client: AxiosInstance,
    request: InternalAxiosRequestConfig<unknown>,
  ): Promise<InternalAxiosRequestConfig<unknown>> {
    const { headers } = request;
    headers.set("X-CSRF-Token", await this.getProof(client));
    return { ...request, headers, withCredentials: true };
  }

  receivedResponse(response: AxiosResponse<unknown, unknown>): void {
    if (
      /\/api\/(auth\/password\/(login|change)|auth\/enrollment\/complete|logout)$/.test(
        response.config.url || "",
      )
    )
      this.reset();
  }

  async retryRejectedMutation(
    client: AxiosInstance,
    error: unknown,
  ): Promise<AxiosResponse<unknown, unknown>> {
    if (!axios.isAxiosError<unknown, unknown>(error)) throw error;
    const request = error.config;
    const data = error.response?.data;
    const invalidProof =
      typeof data === "object" &&
      data !== null &&
      "detail" in data &&
      data.detail === "Invalid CSRF token";
    // Only this precise rejection guarantees the operation was not applied.
    if (
      request &&
      !("nativeCsrfRetried" in request && request.nativeCsrfRetried === true) &&
      error.response?.status === 403 &&
      invalidProof
    ) {
      this.reset();
      const retry: CsrfRequest = { ...request, nativeCsrfRetried: true };
      return client.request<unknown, AxiosResponse<unknown, unknown>, unknown>(
        retry,
      );
    }
    throw error;
  }
}
