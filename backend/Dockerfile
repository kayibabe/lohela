# ---- Stage 1: build the React client -------------------------------------
# The SPA calls the API on same-origin relative paths, so it ships inside this
# image and is served by FastAPI. Build context must be the repo root.
FROM node:20-slim AS frontend

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


# ---- Stage 2: the API image ----------------------------------------------
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8

# postgresql-client-18 matches production's Postgres 18 server exactly (pg_dump
# is not guaranteed to work correctly against a server newer than itself, and
# Debian trixie's own repos only ship client 17 — so add PGDG's apt repo for 18).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    wget \
    gnupg \
    ca-certificates \
    && wget -q -O /etc/apt/trusted.gpg.d/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/etc/apt/trusted.gpg.d/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y wget gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# app/main.py serves this directory when it contains an index.html.
COPY --from=frontend /build/dist ./static

EXPOSE 8000

# Set CELERY_WORKER=1 on the Railway worker service to start the Celery
# consumer instead of the API server. Both run from the same image.
CMD ["sh", "-c", "if [ \"$CELERY_WORKER\" = '1' ]; then exec celery -A app.tasks.pipeline worker --loglevel=info; else exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}; fi"]
