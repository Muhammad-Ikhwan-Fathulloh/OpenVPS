"""
OpenVPS - Modul tambahan: Autentikasi & Rate Limiting
=========================================================
Sengaja dibuat sederhana (API key statis + in-memory token bucket) supaya
tetap mudah dibaca untuk belajar, tapi cukup untuk menutup 2 poin paling
kritis di checklist production lama (docs/deploy_to_vps.md):
  - "Tambah autentikasi (API key/token) di endpoint /relocalize"
  - mencegah 1 klien membanjiri server (relevan sekarang karena endpoint
    deteksi YOLO jauh lebih berat CPU-nya daripada endpoint lama)

Upgrade path production: ganti API key statis dengan OAuth2/JWT per-user
(FastAPI Security + fastapi-users, atau API gateway di depan seperti Kong/
Traefik), dan ganti rate limiter in-memory ini dengan Redis-based (supaya
konsisten kalau server discale jadi >1 instance).
"""

import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Query, WebSocket, WebSocketException, status

import config


def check_api_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> None:
    """Dependency untuk endpoint HTTP biasa. Terima key lewat header
    `X-API-Key` (cara utama) ATAU query param `?api_key=` (fallback, karena
    beberapa konteks browser/klien lebih gampang kirim lewat URL)."""
    if not config.AUTH_ENABLED:
        return  # OPENVPS_API_KEY tidak diset -> mode dev, auth off
    supplied = x_api_key or api_key
    if supplied != config.API_KEY:
        raise HTTPException(status_code=401, detail="API key tidak valid atau tidak diisi (header X-API-Key)")


async def check_api_key_ws(websocket: WebSocket) -> None:
    """Versi untuk WebSocket: browser tidak bisa set header custom saat
    membuka koneksi WS, jadi key dikirim lewat query string
    (wss://.../ws/detect?api_key=...). Tetap aman selama pakai wss:// (TLS)."""
    if not config.AUTH_ENABLED:
        return
    supplied = websocket.query_params.get("api_key")
    if supplied != config.API_KEY:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="API key tidak valid")
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)


class RateLimiter:
    """Token-bucket sederhana per-IP, per-proses (in-memory). Cukup untuk
    1 instance VPS. Reset otomatis lewat sliding window 60 detik."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True


_limiter = RateLimiter(config.RATE_LIMIT_PER_MIN)


def rate_limit(request) -> None:
    """Dependency: panggil dengan `request: Request` lewat Depends() di tiap
    endpoint yang mau dibatasi (biasanya endpoint berat: /detect,
    /relocalize, /recordings)."""
    if not config.RATE_LIMIT_ENABLED:
        return
    client_ip = request.client.host if request.client else "unknown"
    if not _limiter.allow(client_ip):
        raise HTTPException(
            status_code=429,
            detail=f"Terlalu banyak request dari {client_ip}. Coba lagi sebentar lagi.",
        )
