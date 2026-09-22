import { ws } from "msw";
import { server } from "#/mocks/node";

/**
 * Creates a WebSocket link for MSW testing
 * @param url - WebSocket URL to mock (default: "ws://localhost/events/socket")
 * @returns MSW WebSocket link
 */
export const createWebSocketLink = (url = "ws://localhost/events/socket") =>
  ws.link(url);

/**
 * Returns the MSW server that WebSocket handlers should be registered on.
 *
 * MSW >=2.13 dispatches every matching WebSocket handler, and each listening
 * `setupServer()` installs its own interceptor. Creating a second server here
 * (on top of the global one started in `vitest.setup.ts`) makes every
 * connection emit two `connection` events, so reuse the global server.
 */
export const createWebSocketMockServer = (_wsLink: ReturnType<typeof ws.link>) =>
  server;

/**
 * Creates a complete WebSocket testing setup with server and link
 * @param url - WebSocket URL to mock (default: "ws://localhost/events/socket")
 * @returns Object containing the WebSocket link and configured server
 */
export const createWebSocketTestSetup = (
  url = "ws://localhost/events/socket",
) => {
  const wsLink = createWebSocketLink(url);
  const server = createWebSocketMockServer(wsLink);

  return { wsLink, server };
};

/**
 * Standard WebSocket test setup for conversation WebSocket handler tests
 * Updated to use the V1 WebSocket URL pattern: /sockets/events/{conversationId}
 * Uses a wildcard pattern to match any conversation ID
 */
export const conversationWebSocketTestSetup = () =>
  createWebSocketTestSetup("ws://localhost:3000/sockets/events/*");
