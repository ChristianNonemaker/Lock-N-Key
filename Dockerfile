FROM python:3.13-slim AS base

# System deps for psycopg2-binary and Playwright's Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev curl \
        # Playwright Chromium runtime deps
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
        libasound2 libatspi2.0-0 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir . \
    && playwright install chromium \
    && pip cache purge

# Copy application code
COPY dk_ncaab/ dk_ncaab/
COPY api/ api/
COPY ui/ ui/
COPY alembic.ini .

EXPOSE 8000 8501
