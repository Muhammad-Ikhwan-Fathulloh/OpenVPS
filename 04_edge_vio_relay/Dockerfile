# OpenVPS - Module 04 server (relocalization + YOLO detection + replay)
FROM python:3.12-slim

# Dependency native untuk OpenCV & ultralytics/torch
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 03_hloc_lite dibutuhkan server.py (sys.path.append ke folder ini)
COPY 03_hloc_lite /app/03_hloc_lite
COPY 04_edge_vio_relay/requirements.txt /app/04_edge_vio_relay/requirements.txt
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r /app/04_edge_vio_relay/requirements.txt

COPY 04_edge_vio_relay /app/04_edge_vio_relay
WORKDIR /app/04_edge_vio_relay

# Jangan jalankan sebagai root di production
RUN useradd -m -u 1000 openvps \
    && mkdir -p collected_photos recordings \
    && chown -R openvps:openvps /app
USER openvps

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
