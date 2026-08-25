"""
OpenVPS - Konfigurasi Server (Module 04)
==========================================
Semua nilai bisa di-override lewat environment variable, supaya deployment
production tidak perlu ubah kode sama sekali (12-factor config).

Cara pakai (VPS/systemd): taruh di /etc/openvps.env lalu tambahkan
`EnvironmentFile=/etc/openvps.env` di unit systemd-nya.
Cara pakai (Docker): `docker run --env-file .env ...` (lihat .env.example).
"""

import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: list[str]) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default
    return [x.strip() for x in val.split(",") if x.strip()]


BASE_DIR = Path(__file__).resolve().parent

# --- Autentikasi ---
# Kosongkan HANYA untuk dev lokal. Di production WAJIB diisi (auth otomatis
# aktif kalau nilainya tidak kosong) - lihat docs/deploy_to_vps.md.
API_KEY = os.getenv("OPENVPS_API_KEY", "").strip()
AUTH_ENABLED = bool(API_KEY)

# --- Multiset API ---
# Opsional: Jika Anda ingin integrasi langsung ke Multiset.ai Cloud
MULTISET_CLIENT_ID = os.getenv("MULTISET_CLIENT_ID", "").strip()
MULTISET_CLIENT_SECRET = os.getenv("MULTISET_CLIENT_SECRET", "").strip()

# --- CORS ---
# Default "*" supaya gampang dites dari mana saja. GANTI ke domain frontend
# asli kamu di production (mis. "https://vps-kamu.example.com").
CORS_ORIGINS = _list("OPENVPS_CORS_ORIGINS", ["*"])
# CORS spec melarang allow_credentials=True bersamaan dengan origins=["*"].
# Kalau origins spesifik (bukan wildcard), aktifkan credentials supaya
# browser bisa kirim cookie/auth header cross-origin dengan benar.
CORS_ALLOW_CREDENTIALS = CORS_ORIGINS != ["*"]

# --- Peta VPS (hasil Modul 02 COLMAP + Modul 03 hloc-lite) ---
# Kosong = pakai build_demo_map() (data sintetis, HANYA untuk belajar/tes).
MAP_PATH = os.getenv("OPENVPS_MAP_PATH", "").strip()

# --- YOLO / Multi Object Detection ---
YOLO_MODEL = os.getenv("OPENVPS_YOLO_MODEL", "yolov8n.pt")
YOLO_CONF = float(os.getenv("OPENVPS_YOLO_CONF", "0.35"))
YOLO_IOU = float(os.getenv("OPENVPS_YOLO_IOU", "0.45"))
YOLO_DEVICE = os.getenv("OPENVPS_YOLO_DEVICE", "cpu")  # "cpu", "cuda:0", "mps", dst
YOLO_IMG_SIZE = int(os.getenv("OPENVPS_YOLO_IMGSZ", "640"))
YOLO_MAX_DET = int(os.getenv("OPENVPS_YOLO_MAX_DET", "50"))
# Kalau CPU VPS lemah: set >1 supaya deteksi tidak dijalankan tiap frame
# (frame yang di-skip tetap disimpan untuk replay, tapi tanpa deteksi baru -
# player replay otomatis "menahan" box terakhir di antara frame yang di-skip).
DETECT_EVERY_N_FRAMES = max(1, int(os.getenv("OPENVPS_DETECT_EVERY_N", "1")))

# --- Storage ---
UPLOAD_DIR = Path(os.getenv("OPENVPS_UPLOAD_DIR", str(BASE_DIR / "collected_photos")))
RECORDINGS_DIR = Path(os.getenv("OPENVPS_RECORDINGS_DIR", str(BASE_DIR / "recordings")))
MAX_UPLOAD_MB = int(os.getenv("OPENVPS_MAX_UPLOAD_MB", "15"))
MAX_WS_FRAME_MB = int(os.getenv("OPENVPS_MAX_WS_FRAME_MB", "8"))

# --- Rate limiting (sederhana, in-memory per-proses; cukup untuk 1 VPS.
# Kalau scale ke multi-instance, ganti ke Redis-based limiter). ---
RATE_LIMIT_ENABLED = _bool("OPENVPS_RATE_LIMIT_ENABLED", True)
RATE_LIMIT_PER_MIN = int(os.getenv("OPENVPS_RATE_LIMIT_PER_MIN", "120"))

# --- Logging ---
LOG_LEVEL = os.getenv("OPENVPS_LOG_LEVEL", "INFO")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
