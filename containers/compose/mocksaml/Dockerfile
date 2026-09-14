# The source and lockfile come from the pinned upstream Compose build context.
FROM node:22.16-alpine3.21@sha256:9f3ae04faa4d2188825803bf890792f33cc39033c9241fc6bb201149470436ca AS builder
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY --from=upstream /package.json /package-lock.json ./
RUN npm ci
COPY --from=upstream / .
COPY patch.cjs /tmp/patch.cjs
RUN node /tmp/patch.cjs
ENV NEXT_PUBLIC_GTM_ID="" \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22.16-alpine3.21@sha256:9f3ae04faa4d2188825803bf890792f33cc39033c9241fc6bb201149470436ca AS runner
WORKDIR /app
ENV NODE_OPTIONS="--max-http-header-size=81920" \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=4000
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 4000
CMD ["node", "server.js"]
