# OpenVPS - Module 04 server (relocalization + YOLO detection + replay)
FROM python:3.12-slim

# Dependency native untuk OpenCV & ultralytics/torch
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Folder ini sudah berdiri sendiri (hloc_lite.py sudah disalin ke sini),
# jadi tidak perlu lagi COPY dari folder modul lain.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Jangan jalankan sebagai root di production
RUN useradd -m -u 1000 openvps \
    && mkdir -p collected_photos recordings \
    && chown -R openvps:openvps /app
USER openvps

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
