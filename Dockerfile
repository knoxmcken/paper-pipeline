# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# Install the package (with the 'web' extra) before copying the rest so
# dependency layers stay cached across code-only changes.
COPY pyproject.toml README.md ./
COPY paperpipe ./paperpipe
RUN pip install --no-cache-dir ".[web]"

# Cloud Run's filesystem is ephemeral: this is only a fallback so the app
# still boots (and serves an empty corpus) if no volume is mounted at /data.
# See README "Deploying to Cloud Run" for persisting real data via GCS FUSE.
ENV PAPERPIPE_DATA=/data \
    PORT=8080

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8080

CMD exec paperpipe serve --host 0.0.0.0 --port "${PORT}"
