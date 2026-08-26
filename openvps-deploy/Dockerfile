# OpenVPS - Module 04 server (relocalization + YOLO detection + replay)
# Production-optimized Dockerfile with multi-stage for smaller final image,
# proper healthcheck grace period for slow YOLO/torch load, and uvicorn
# production tuning.

# ================== STAGE 1: Builder (pip wheel cache) ==================
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build deps for opencv/native wheels (temporary, not in final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake pkg-config \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Torch CPU + app requirements: build wheels once, reuse
COPY requirements.txt /build/requirements.txt
RUN pip install --upgrade pip wheel setuptools \
    && pip wheel --no-cache-dir --wheel-dir /wheels \
        torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r /build/requirements.txt

# ================== STAGE 2: Runtime (final, small) ==================
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="OpenVPS Server" \
      org.opencontainers.image.description="Visual Positioning System + YOLO Detection + Replay" \
      org.opencontainers.image.vendor="OpenVPS"

# ----- Runtime-only native deps (no build toolchain) -----
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip

WORKDIR /app

# ----- Install pre-built wheels (fast, no gcc in final image) -----
COPY --from=builder /wheels /wheels
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --no-index --find-links=/wheels \
        torch torchvision \
    && pip install --no-cache-dir --no-index --find-links=/wheels \
        -r /app/requirements.txt \
    && rm -rf /wheels

# ----- App source -----
COPY . /app

# ----- Non-root user + writable directories -----
RUN useradd -m -u 1000 openvps \
    && mkdir -p collected_photos recordings uploads \
    && chown -R openvps:openvps /app \
    && chmod -R u+rwX collected_photos recordings uploads

USER openvps

# ----- Default env (override via docker run -e or compose) -----
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENVPS_HOST=0.0.0.0 \
    OPENVPS_PORT=8000

EXPOSE 8000

# ----- Healthcheck: start-period 180s = allow 3 menit load YOLO + map -----
# YOLO CPU + torch first-load bisa 60-120 detik tergantung VPS CPU/RAM.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 || exit 1

# ----- Uvicorn production config: -----
#   --workers 1-2   -> cukup karena YOLO inference CPU-bound (1 worker per 1-2 vCPU)
#   --timeout-keep-alive 75 -> lebih besar dari default nginx 60
#   --ws websockets -> auto-choose uvicorn standard ws
#   --h11-max-incomplete-event-size 6291456 -> handle upload 6MB per frame WS
CMD ["uvicorn", "server:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "300", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
