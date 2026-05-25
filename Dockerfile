FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY app/ ./app/

RUN pip install --user --no-cache-dir . && \
    pip install --user --no-cache-dir alembic

FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends fonts-dejavu-core && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

WORKDIR /app

COPY --from=builder /root/.local /home/app/.local
COPY . .

RUN mkdir -p /app/uploads && chown -R app:app /app

USER app

ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
