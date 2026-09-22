# Multi-stage build for the 508-agent frontend (Expo Router static web export).
# Stage 1: build the static bundle.
# Stage 2: serve it from nginx on port 8080 (CDN/runtime-friendly).
#
# Build context is THIS directory (docker-compose sets context ./frontend/frontend).

# ---------- Stage 1: builder ----------
FROM node:20-alpine AS builder

WORKDIR /app

# Install deps first for better layer caching.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Copy the rest of the source.
COPY . .

# EXPO_PUBLIC_* vars must be present at build time so they get inlined
# into the static bundle. Pass via --build-arg at docker build time.
ARG EXPO_PUBLIC_API_URL
ENV EXPO_PUBLIC_API_URL=${EXPO_PUBLIC_API_URL}
# The public site origin (https://app.example.com). Needed at build time to
# write an absolute-URL sitemap.xml; without it the sitemap step is skipped.
ARG PUBLIC_BASE_URL
ENV PUBLIC_BASE_URL=${PUBLIC_BASE_URL}
# Same origin, inlined into the bundle for <link rel="canonical"> (Seo.tsx).
ENV EXPO_PUBLIC_SITE_URL=${PUBLIC_BASE_URL}

RUN npx expo export -p web --output-dir dist
# expo-router's debug route index lists every screen (admin included); it is
# not a page anyone should land on from search.
RUN rm -f dist/_sitemap.html
RUN node scripts/generate-sitemap.mjs dist
# Fails the build if navigation stopped rendering as crawlable <a href>, or a
# link points at a page that didn't export.
RUN node scripts/check-internal-links.mjs dist
# Fails the build if any palette text/background pair drops below WCAG AA.
RUN node scripts/check-contrast.mjs
# Fails the build if pixel art spreads beyond the logo, a third-party or
# pixel font sneaks in, or the first-visit tour starts auto-opening again.
RUN node scripts/check-design.mjs
# Routing (/pricing -> pricing.html), security headers and a CSP whose
# inline-script hashes come from this exact bundle.
RUN node scripts/render-nginx-conf.mjs dist /app/nginx.conf

# ---------- Stage 2: runtime ----------
FROM nginx:alpine AS runtime

RUN rm -f /etc/nginx/conf.d/default.conf
COPY --from=builder /app/nginx.conf /etc/nginx/conf.d/app.conf

# Copy the static bundle.
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
