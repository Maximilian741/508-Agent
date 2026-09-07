# Multi-stage build for the 508-agent frontend (Expo Router static web export).
# Stage 1: build the static bundle.
# Stage 2: serve it from nginx on port 8080 (CDN/runtime-friendly).

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

RUN npx expo export -p web --output-dir dist
RUN node scripts/generate-sitemap.mjs dist

# ---------- Stage 2: runtime ----------
FROM nginx:alpine AS runtime

# Replace default config so nginx listens on 8080 and SPA-routes fall back to index.html.
RUN rm -f /etc/nginx/conf.d/default.conf
RUN printf '%s\n' \
    'server {' \
    '    listen 8080;' \
    '    server_name _;' \
    '    root /usr/share/nginx/html;' \
    '    index index.html;' \
    '    location / {' \
    '        try_files $uri $uri/ /index.html;' \
    '    }' \
    '    location ~* \.(?:js|css|png|jpg|jpeg|gif|svg|woff2?|ttf|eot|ico)$ {' \
    '        expires 1y;' \
    '        access_log off;' \
    '        add_header Cache-Control "public, immutable";' \
    '    }' \
    '}' > /etc/nginx/conf.d/app.conf

# Copy the static bundle.
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
