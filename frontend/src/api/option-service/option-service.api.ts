import { openHands } from "../open-hands-axios";
import { ModelsResponse, ServerInfo, WebClientConfig } from "./option.types";

/**
 * Service for handling API options endpoints
 */
class OptionService {
  /**
   * Retrieve the structured models response from the backend.
   *
   * The backend is the single source of truth for verified models,
   * verified providers, and provider assignment for bare model names.
   */
  static async getModels(): Promise<ModelsResponse> {
    const { data } = await openHands.get<ModelsResponse>("/api/options/models");
    return data;
  }

  /**
   * Retrieve the list of security analyzers available
   * @returns List of security analyzers available
   */
  static async getSecurityAnalyzers(): Promise<string[]> {
    const { data } = await openHands.get<string[]>(
      "/api/options/security-analyzers",
    );
    return data;
  }

  /**
   * Get the web client configuration from the server
   * @returns Web client configuration response
   */
  static async getConfig(): Promise<WebClientConfig> {
    const { data } = await openHands.get<WebClientConfig>(
      "/api/v1/web-client/config",
    );
    return data;
  }

  /**
   * Get server version/runtime info from the unauthenticated ``/server_info``
   * endpoint (``openhands/app_server/status/status_router.py``). Used to
   * surface the Enterprise server and agent SDK versions in the UI.
   * @returns Server info response
   */
  static async getServerInfo(): Promise<ServerInfo> {
    const { data } = await openHands.get<ServerInfo>("/server_info");
    return data;
  }
}

export default OptionService;
