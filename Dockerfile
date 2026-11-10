# One container: FastAPI backend + the built React dashboard. Build from the repo root:
#   docker build -t ubti-voice .        (or in Azure:  az acr build -r <registry> -t ubti-voice:v2 .)

# ---------- dashboard ----------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- backend ----------
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STATIC_DIR=/app/static TMP_DIR=/tmp/ubti UPLOAD_DIR=/app/uploads

# Microsoft ODBC Driver 18 (Fabric SQL) + ffmpeg (MP3 recordings, voice-clone audio)
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates ffmpeg unixodbc \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist ./static

RUN useradd -m -u 10001 ubti && mkdir -p /tmp/ubti /app/uploads && chown -R ubti /tmp/ubti /app/uploads
USER ubti
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fs http://localhost:8000/health || exit 1
# ONE worker per container: live calls keep state in the process. Scale with more replicas + REDIS_URL.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--ws-ping-interval", "20", "--timeout-graceful-shutdown", "60"]
