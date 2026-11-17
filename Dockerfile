# syntax=docker/dockerfile:1.7
FROM python:3.14.0-alpine3.22@sha256:8373231e1e906ddfb457748bfc032c4c06ada8c759b7b62d9c73ec2a3c56e710 AS builder

WORKDIR /app
COPY requirements.txt .
RUN apk add --no-cache build-base \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.14.0-alpine3.22@sha256:8373231e1e906ddfb457748bfc032c4c06ada8c759b7b62d9c73ec2a3c56e710 AS runtime

RUN apk add --no-cache sqlite \
    && addgroup -g 1000 app \
    && adduser -D -u 1000 -G app app \
    && mkdir -p /data/images /data/backups \
    && chown -R app:app /data

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY static ./static
COPY scripts ./scripts
RUN chmod +x scripts/backup.sh && chown -R app:app /app

USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
