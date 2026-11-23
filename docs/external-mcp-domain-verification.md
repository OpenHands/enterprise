# OpenAI plugin domain verification

Configure `OPENAI_APPS_CHALLENGE_TOKEN` on the enterprise server with the exact
verification token displayed by the OpenAI submission portal. This is a public
ownership challenge, separate from OAuth client credentials and user passwords.
Do not hard-code a deployment's token in application source.

The server returns the token as plain text, without a JSON wrapper or trailing
newline, at:

```text
GET https://<mcp-host>/.well-known/openai-apps-challenge
```

The route is unauthenticated and registered before the frontend catch-all. It
works before MCP clients/OAuth are configured. When the token is unset or blank,
it returns 404 rather than the SPA's HTML. Responses use `Cache-Control: no-store`.

Route the origin-root path to the enterprise server, even when the MCP endpoint
itself lives at `/external-mcp/mcp`. Configure the token for the intended host and
plugin; do not overwrite an existing plugin's challenge token to verify another
plugin. OpenAI also permits an eligible parent hostname as the challenge base.

After deploying an image containing this endpoint and the token setting, check
that the public URL returns HTTP 200 with `Content-Type: text/plain` and the exact
portal token. Then choose **Verify Domain** in the submission portal. OAuth
connection and tool discovery are subsequent steps.
