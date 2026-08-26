# OpenVPS - CPU-only, production-optimized Dockerfile
# Strategi optimasi:
#   1. Multi-stage build: builder (pip wheel) -> runtime (slim final image)
#   2. Torch CPU-only wheel dari whl/cpu (tidak ada CUDA, hemat ~2GB)
#   3. opencv-python-headless (tanpa Qt/GTK UI libs)
#   4. Layer caching: torch di-install TERPISAH dari requirements.txt supaya
#      layer torch tidak di-rebuild kalau hanya requirements.txt yang berubah
#   5. pip install --no-compile: skip .pyc generation saat install (hemat space)
#   6. Bersihkan cache pip, wheel, dan __pycache__ di satu RUN layer

# ================== STAGE 1: Builder ==================
FROM python:3.12-slim AS builder

WORKDIR /build

# Build deps hanya untuk native extensions (opencv, dll)
# Digabung dalam 1 RUN supaya tidak ada layer intermediate yang mubazir
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# --- Layer 1: Torch CPU-only (besar ~700MB, jarang berubah -> cache lama) ---
RUN pip install --upgrade pip wheel setuptools --quiet \
    && pip wheel --no-cache-dir --wheel-dir /wheels \
        "torch==2.3.1+cpu" \
        "torchvision==0.18.1+cpu" \
        --index-url https://download.pytorch.org/whl/cpu

# --- Layer 2: App requirements (sering berubah -> layer terpisah) ---
COPY requirements.txt /build/requirements.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r /build/requirements.txt

# ================== STAGE 2: Runtime (final image) ==================
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="OpenVPS Server" \
      org.opencontainers.image.description="Visual Positioning System + YOLO Detection + Replay (CPU)" \
      org.opencontainers.image.vendor="OpenVPS"

# Runtime native deps only (tidak ada gcc/build-essential di image final)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip --quiet

WORKDIR /app

# Install dari wheels (tidak perlu internet, tidak ada compiler)
COPY --from=builder /wheels /wheels
COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --no-index --find-links=/wheels \
        "torch==2.3.1+cpu" \
        "torchvision==0.18.1+cpu" \
    && pip install --no-cache-dir --no-index --find-links=/wheels \
        -r /app/requirements.txt \
    # Bersihkan wheels, .pyc, dan cache yang tidak perlu di image final
    && rm -rf /wheels \
    && find /usr/local/lib/python3.12 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
    && find /usr/local/lib/python3.12 -name "*.pyc" -delete 2>/dev/null || true

# App source (dipisah agar layer di atas bisa di-cache)
COPY . /app

# Non-root user + direktori writable
RUN useradd -m -u 1000 openvps \
    && mkdir -p collected_photos recordings uploads \
    && chown -R openvps:openvps /app \
    && chmod -R u+rwX collected_photos recordings uploads

USER openvps

# Default env
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OPENVPS_HOST=0.0.0.0 \
    OPENVPS_PORT=8000 \
    # Matikan ultralytics telemetry
    YOLO_TELEMETRY=False

EXPOSE 8000

# Grace period 180s karena YOLO + torch cold-start di CPU bisa 60-120 detik
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 || exit 1

CMD ["uvicorn", "server:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "300", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
