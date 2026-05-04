# syntax=docker/dockerfile:1.6
# Multi-stage frontend image: build the Expo web bundle, then serve it
# with nginx.  We build inside the container so the host OS doesn't need
# Node + Expo CLI installed.

FROM node:20-bookworm-slim AS builder
ENV CI=1 \
    NODE_ENV=production

WORKDIR /build

# Copy manifests first so docker can cache the install layer.
COPY frontend/frontend/package.json frontend/frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

# Copy the rest of the frontend source.
COPY frontend/frontend ./

# Expo's static export drops the bundle into ./dist.
RUN npx expo export -p web --output-dir dist


FROM nginx:alpine AS runtime

# Drop the default nginx config — we ship our own that serves the SPA
# correctly, with sensible cache headers.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY deploy/nginx.conf /etc/nginx/conf.d/app.conf

COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
