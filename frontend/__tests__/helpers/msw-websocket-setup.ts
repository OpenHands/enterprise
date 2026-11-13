import { ws } from "msw";
import { server as globalServer } from "#/mocks/node";

/**
 * Creates a WebSocket link for MSW testing
 * @param url - WebSocket URL to mock (default: "ws://localhost/events/socket")
 * @returns MSW WebSocket link
 */
export const createWebSocketLink = (url = "ws://localhost/events/socket") =>
  ws.link(url);

/**
 * Standard WebSocket test setup for conversation WebSocket handler tests
 * Updated to use the V1 WebSocket URL pattern: /sockets/events/{conversationId}
 * Uses a wildcard pattern to match any conversation ID
 *
 * Reuses the global MSW server from `vitest.setup.ts` instead of creating a
 * second `setupServer()`. MSW >= 2.13 shares interceptor sources between
 * servers, so a second server would register WebSocket connection handlers
 * twice and make every connection handler fire multiple times.
 */
export const conversationWebSocketTestSetup = () => {
  const wsLink = createWebSocketLink("ws://localhost:3000/sockets/events/*");

  return { wsLink, server: globalServer };
};
