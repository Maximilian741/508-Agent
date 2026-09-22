# syntax=docker/dockerfile:1.6
# Multi-stage backend image.
#
# Stage 1 (builder) installs the Python deps into a virtualenv so the
# runtime stage stays slim.  Stage 2 copies the venv + source, drops
# privileges, exposes 8000, and adds a healthcheck against /healthz.

FROM python:3.13-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt


FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# curl is used for the healthcheck below.
# tesseract-ocr + the English pack back OCR_ENABLED: pytesseract is already
# in requirements, but the provider's available() check needs the BINARY, and
# without it OCR_ENABLED=true silently did nothing (get_ocr_provider logs once
# and returns None). Scanned government forms are common enough that shipping
# the ~30MB is worth it. Add tesseract-ocr-<lang> packs here for other languages.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl poppler-utils libgl1 libxml2 libxslt1.1 \
      tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app --home /app --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app backend /app

# Persist runtime artifacts (sqlite, pipeline outputs, materialized cache)
# under a known path that compose mounts as a volume.
RUN mkdir -p /app/.runtime \
 && chown -R app:app /app/.runtime

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

# Run migrations before boot. A failed migration MUST abort startup (no
# `|| true`) rather than silently serving against a stale/half-migrated schema.
# UVICORN_WORKERS: analysis/remediation run synchronously in-request and are
# CPU-bound, so under load capacity == worker count (measured: on 1 worker, 4
# concurrent analyses each took 4.1x solo; on 2, 2.1x). 2 is right for a small
# VPS; on a bigger box set UVICORN_WORKERS toward the core count in .env.
CMD ["sh", "-c", "alembic -c /app/alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-2}"]
