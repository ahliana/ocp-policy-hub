# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: build the React frontend as static files.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS frontend-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# Empty (not unset) so the built app calls the API on its own origin
# instead of the http://localhost:8000 dev default — see
# frontend/src/config/api.js. No source maps in the shipped image.
ENV REACT_APP_API_BASE_URL=""
ENV GENERATE_SOURCEMAP=false
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: the API server, serving both /api/* and the built frontend.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Browsers install system-wide (as root, below) but must be usable by
    # the non-root runtime user — without this, Playwright looks in the
    # runtime user's home cache and finds nothing.
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# `playwright install --with-deps` needs apt as root; done before the
# non-root user is created and switched to below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml constraints.txt ./
COPY src/ ./src/
COPY config/ ./config/

# The crawler needs a real browser for JS-rendered sites (see
# src/core/crawler.py); scans run inside this container, so the image
# accepts the Chromium size cost rather than requiring a host install.
# constraints.txt pins every dependency to the version last verified in
# dev (see that file's header for how to regenerate it) so a rebuild
# can't silently pick up a new transitive version.
RUN pip install --no-cache-dir ".[browser]" -c constraints.txt \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

COPY --from=frontend-build /app/frontend/build ./frontend/build

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
