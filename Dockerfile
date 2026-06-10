FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app/ ./app/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir .

FROM alpine:3.20 AS assets

# Vendor HTMX, Alpine.js (CSP build) and Fraunces/Inter web fonts.
# Pinned versions; integrity is implicit via HTTPS to known CDNs.
RUN apk add --no-cache curl ca-certificates

WORKDIR /vendor

ARG HTMX_VERSION=2.0.4

# Alpine CSP build: version pinned INLINE in the URL, not via an ARG. A change
# to an ARG default does not reliably bust the BuildKit/GHA layer cache, so the
# 3.14.8 -> 3.15.12 bump was silently served from cache (u3s.7). Editing the RUN
# text directly changes the layer hash and forces a fresh download on bump.
# 3.15+ ships the extended CSP parser (ternaries, arithmetic, object literals);
# 3.14.8's parser was property/method-access only.
RUN mkdir -p js fonts && \
    curl -fsSL -o js/htmx.min.js \
        "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" && \
    curl -fsSL -o js/alpine.min.js \
        "https://cdn.jsdelivr.net/npm/@alpinejs/csp@3.15.12/dist/cdn.min.js"

# Web fonts via Fontsource (WOFF2, self-hostable, OFL-licensed).
RUN curl -fsSL -o fonts/fraunces-variable.woff2 \
        "https://cdn.jsdelivr.net/fontsource/fonts/fraunces:vf@latest/latin-wght-normal.woff2" && \
    curl -fsSL -o fonts/inter-variable.woff2 \
        "https://cdn.jsdelivr.net/fontsource/fonts/inter:vf@latest/latin-wght-normal.woff2"

FROM node:20-alpine AS css-builder

WORKDIR /css
COPY tailwind.config.js ./
COPY frontend/static/css/input.css ./input.css
COPY app/templates/ ./app/templates/
COPY frontend/static/spikes/ ./frontend/static/spikes/

RUN npm install --no-save --no-audit --no-fund tailwindcss@3.4.17 \
    && npx tailwindcss -c tailwind.config.js -i ./input.css -o ./styles.css --minify

FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY . .
COPY --from=css-builder /css/styles.css /app/frontend/static/css/styles.css
COPY --from=assets /vendor/js/htmx.min.js /app/frontend/static/js/htmx.min.js
COPY --from=assets /vendor/js/alpine.min.js /app/frontend/static/js/alpine.min.js
COPY --from=assets /vendor/fonts/ /app/frontend/static/fonts/

RUN mkdir -p /app/uploads && chown -R app:app /app

USER app

# Per-build cache-busting token for static assets (?v=...). Passed in by CI as a
# build timestamp; falls back to "dev" for local builds. A fresh value per image
# busts GHA-layer, origin and CDN caches together (the u3s.7 deploy trap).
ARG ASSET_VERSION=dev

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ASSET_VERSION=${ASSET_VERSION}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
