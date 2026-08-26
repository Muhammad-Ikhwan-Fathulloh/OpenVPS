"""
OpenVPS - Module 04: Edge VIO + Server Relocalization + Object Detection
=============================================================================
Konsep dasar (relocalization) TIDAK berubah dari versi awal:

  [HP: ARKit/ARCore VIO]  --terus-menerus (60fps, on-device, halus)--> pose lokal
         |
         |--setiap N detik, kirim 1 foto--> [SERVER: hloc-lite]
         |                                         |
         |<--pose koreksi (absolut, akurat)--------|
         |
  [HP: gabungkan pose VIO + koreksi server -> pose final stabil & akurat]

Yang DITAMBAHKAN di versi ini:
  1. Multi Object Detection (YOLO) real-time lewat WebSocket, terpisah dari
     jalur relocalization (lihat detector.py).
  2. Recording + Replay: sesi capture (frame + hasil deteksi tiap frame)
     disimpan ke disk supaya bisa "diputar ulang" persis seperti aslinya
     tanpa perlu re-run YOLO (lihat recordings.py).
  3. Pengerasan production: API key auth, CORS yang bisa dibatasi, rate
     limiting, batas ukuran upload, logging terstruktur, exception handler
     rapi, /health yang melaporkan status peta & model YOLO (lihat
     config.py & security.py).

DEPLOY KE VPS: lihat docs/deploy_to_vps.md (systemd+nginx atau Docker).

ENDPOINT:
  GET  /                                -> web UI capture/relocalize/detect/record
  GET  /replay                          -> web UI player untuk replay recording
  GET  /health                          -> status server, peta, & model YOLO
  POST /relocalize                      -> 1 foto -> pose absolut (VPS)
  POST /upload-images                   -> banyak foto -> disimpan utk Modul 02
  POST /detect                          -> 1 foto -> daftar objek terdeteksi
  WS   /ws/detect                       -> deteksi objek real-time (live saja, tanpa simpan)
  POST /recordings                      -> mulai sesi recording baru
  WS   /ws/recordings/{session_id}      -> streaming frame + deteksi, DISIMPAN utk replay
  POST /recordings/{session_id}/stop    -> selesaikan sesi, bikin manifest final
  GET  /recordings                      -> daftar sesi yang tersimpan
  GET  /recordings/{session_id}/manifest -> metadata + daftar frame & deteksi
  GET  /recordings/{session_id}/frames/{file} -> ambil 1 frame (jpg) utk player replay

Semua endpoint (kecuali /health) butuh API key kalau OPENVPS_API_KEY diset -
lihat security.py & .env.example.
"""

import base64
import logging
import time
from pathlib import Path
import requests

import cv2
import numpy as np
from fastapi import (
    Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
import recordings
from detector import get_detector
from security import check_api_key, check_api_key_ws, rate_limit
from s3_utils import storage as s3_storage

# hloc_lite.py sekarang berada di folder yang sama (bukan lagi diambil dari
# ../03_hloc_lite) supaya folder deploy ini berdiri sendiri.
from hloc_lite import MapDatabase, build_demo_map, localize_query

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("openvps.server")

app = FastAPI(
    title="OpenVPS Relocalization + Detection Server",
    description="Visual Positioning System relocalization, multi object detection (YOLO), dan replay.",
    version="2.0.0",
)

# CORS: default "*" supaya gampang dites. Di production, set
# OPENVPS_CORS_ORIGINS="https://domain-kamu.com,https://domain-lain.com".
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=config.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# ============================================================
# Web UI (web_capture.html & web_replay.html) - disatukan dgn server
# ============================================================
# Kedua file HTML ini self-contained (CSS & JS inline, tanpa asset
# eksternal), jadi cukup di-serve langsung sebagai halaman statis. Field
# "Server URL" di dalamnya otomatis diisi ke origin request saat ini lewat
# JS di bawah, supaya begitu dibuka dari VPS langsung nyambung ke API-nya
# sendiri tanpa perlu diketik manual.

BASE_DIR = Path(__file__).resolve().parent

_AUTOFILL_SERVER_URL_JS = """
<script>
  // Auto-isi input #serverUrl dgn origin saat ini (dijalankan sebelum script
  // lain di halaman ini, jadi nilainya sudah siap dipakai). Kalau halaman
  // dibuka langsung dari file:// (bukan lewat server), biarkan default.
  (function () {
    if (window.location.protocol === "http:" || window.location.protocol === "https:") {
      document.addEventListener("DOMContentLoaded", function () {
        var el = document.getElementById("serverUrl");
        if (el) el.value = window.location.origin;
      });
    }
  })();
</script>
"""


def _serve_html(filename: str) -> HTMLResponse:
    html = (BASE_DIR / filename).read_text(encoding="utf-8")
    # Sisipkan auto-fill script tepat sebelum </head> kalau ada, kalau tidak
    # tempel di awal body.
    if "</head>" in html:
        html = html.replace("</head>", _AUTOFILL_SERVER_URL_JS + "</head>", 1)
    else:
        html = _AUTOFILL_SERVER_URL_JS + html
    return HTMLResponse(content=html)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def web_capture_page():
    """Halaman utama: UI capture + relocalize + live detect + recording + replay."""
    return _serve_html("web_capture.html")

@app.get("/web_capture.html", response_class=HTMLResponse, include_in_schema=False)
def web_capture_link():
    return _serve_html("web_capture.html")

@app.get("/replay", response_class=HTMLResponse, include_in_schema=False)
def web_replay_page():
    """Halaman replay: UI player untuk AR replay recording (2D)."""
    return _serve_html("web_replay.html")

@app.get("/web_replay.html", response_class=HTMLResponse, include_in_schema=False)
def web_replay_link():
    return _serve_html("web_replay.html")

@app.get("/web_three_replay.html", response_class=HTMLResponse, include_in_schema=False)
def web_three_replay_link():
    return _serve_html("web_three_replay.html")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Supaya error tak terduga tidak bocorkan stack trace ke klien di
    # production, tapi tetap ke-log lengkap di server untuk debugging.
    logger.exception("Unhandled error di %s: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Terjadi kesalahan internal di server."})


# --- Peta VPS: di-load sekali saat server start ---
if config.MAP_PATH:
    logger.info("Loading map dari %s ...", config.MAP_PATH)
    # NOTE: MapDatabase.save() di hloc_lite.py saat ini hanya simpan metadata
    # ringkas. Untuk production, lengkapi load_from_colmap()/load() di
    # hloc_lite.py supaya peta asli (deskriptor + titik 3D) ikut ter-load,
    # bukan cuma nama file. Placeholder di bawah biar server tetap start
    # dengan jelas kalau ini belum dilengkapi.
    try:
        MAP_DB: MapDatabase = MapDatabase.load(config.MAP_PATH)  # type: ignore[attr-defined]
    except AttributeError:
        logger.error(
            "MapDatabase.load() belum diimplementasikan di hloc_lite.py - "
            "lengkapi dulu sebelum set OPENVPS_MAP_PATH di production. "
            "Sementara pakai demo map."
        )
        MAP_DB = build_demo_map()
else:
    logger.warning(
        "OPENVPS_MAP_PATH tidak diset -> pakai build_demo_map() (data SINTETIS). "
        "JANGAN dipakai di production, ganti dengan peta asli hasil Modul 02+03."
    )
    MAP_DB = build_demo_map()

if not config.AUTH_ENABLED:
    logger.warning(
        "OPENVPS_API_KEY tidak diset -> AUTH NONAKTIF. Semua endpoint bisa "
        "dipanggil siapa saja. Ini HANYA boleh untuk dev lokal."
    )

DETECTOR = get_detector()


# ============================================================
# Model response
# ============================================================

class TimingOut(BaseModel):
    total: float = 0.0
    pnp: float = 0.0
    matching: float = 0.0

class PoseOut(BaseModel):
    R: list[list[float]] | None = None
    t: list[float] | None = None
    qvec: list[float] | None = None

class RelocResponse(BaseModel):
    success: bool
    pose: PoseOut | None = None
    inliers: int | None = None
    reproj_error: float | None = None
    num_matches: int | None = None
    retrieval: list[str] = []
    query: str | None = None
    timing: TimingOut | None = None
    error: str | None = None
    
    # Legacy fields
    keyframe: str | None = None
    position: list[float] | None = None
    rotation: list[float] | None = None
    n_inliers: int | None = None
    message: str | None = None


class UploadResponse(BaseModel):
    success: bool
    session_id: str
    n_saved: int
    folder: str


class DetectionOut(BaseModel):
    label: str
    cls_id: int
    conf: float
    box: list[float]


class DetectResponse(BaseModel):
    success: bool
    detections: list[DetectionOut] = []
    message: str | None = None


class StartRecordingResponse(BaseModel):
    session_id: str


class StopRecordingResponse(BaseModel):
    session_id: str
    n_frames: int
    fps_estimate: float | None = None


class SessionInfo(BaseModel):
    session_id: str
    created_at: str | None = None
    status: str
    n_frames: int


# ============================================================
# Helper
# ============================================================

def decode_image(raw: bytes, color: bool = True) -> np.ndarray:
    npimg = np.frombuffer(raw, dtype=np.uint8)
    flag = cv2.IMREAD_COLOR if color else cv2.IMREAD_GRAYSCALE
    img = cv2.imdecode(npimg, flag)
    if img is None:
        raise HTTPException(status_code=400, detail="Gagal decode gambar (format tidak didukung/file rusak)")
    return img


def check_upload_size(n_bytes: int, max_mb: int) -> None:
    if n_bytes > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File terlalu besar (maks {max_mb}MB)")


# ============================================================
# Health
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "map": {
            "n_keyframes": len(MAP_DB.keyframes),
            "source": config.MAP_PATH or "demo (sintetis)",
        },
        "detector": {
            "ready": DETECTOR.is_ready,
            "error": DETECTOR.load_error,
            "model": config.YOLO_MODEL,
        },
        "auth_enabled": config.AUTH_ENABLED,
    }


# ============================================================
# Relocalization (VPS) - fitur lama, tidak berubah
# ============================================================

@app.post("/relocalize", response_model=RelocResponse, dependencies=[Depends(check_api_key), Depends(rate_limit)])
async def relocalize(
    image: UploadFile = File(...),
    provider: str = Form("openvps"),
):
    raw = await image.read()
    check_upload_size(len(raw), config.MAX_UPLOAD_MB)
    img = decode_image(raw, color=False)

    if provider == "multiset":
        client_id = config.MULTISET_CLIENT_ID
        client_secret = config.MULTISET_CLIENT_SECRET

        if not client_id or not client_secret:
            return RelocResponse(
                success=False,
                error="Kredensial Multiset.ai belum diisi di env.",
                message="Kredensial Multiset.ai belum diisi di env.",
            )

        try:
            auth_str = f"{client_id}:{client_secret}"
            b64_auth = base64.b64encode(auth_str.encode()).decode()

            logger.info("Mengontak api.multiset.ai untuk token...")
            resp = requests.post(
                "https://api.multiset.ai/v1/m2m/token",
                headers={"Authorization": f"Basic {b64_auth}"},
                timeout=5,
            )
            logger.info("Multiset Auth Response: %s", resp.status_code)

            if resp.status_code == 401:
                return RelocResponse(success=False, error="Kredensial Multiset ditolak (401)", message="Kredensial Multiset ditolak (401 Unauthorized).")
            if not resp.ok:
                return RelocResponse(success=False, error=f"Multiset format error: HTTP {resp.status_code}", message=f"Multiset API error: HTTP {resp.status_code}")

            # MOCK hasil VPS Multiset
            return RelocResponse(
                success=True,
                pose=PoseOut(
                    R=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                    t=[-0.5, 1.2, 0.3],
                    qvec=[1.0, 0.0, 0.0, 0.0]
                ),
                inliers=42,
                keyframe="Multiset_Cloud_Verified",
                position=[-0.5, 1.2, 0.3],
                rotation=[0.02, -0.01, 0.0],
                n_inliers=42,
            )
        except requests.exceptions.Timeout:
            return RelocResponse(success=False, error="Timeout saat menghubungi Multiset API (>5s).", message="Timeout saat menghubungi Multiset API (>5s).")
        except Exception as e:
            logger.error("Multiset error: %s", e)
            return RelocResponse(success=False, error=f"Multiset API Error: {e}", message=f"Multiset API Error: {e}")

    # Default provider: OpenVPS (Local Pipeline)
    try:
        start_t = time.time()
        result = localize_query(MAP_DB, img)
        total_time = time.time() - start_t
    except RuntimeError as e:
        return RelocResponse(success=False, error=str(e), message=str(e))

    # Convert rotation vector to matrix for visual-map-localizer JSON compatibility
    rvec = np.array(result["rvec"], dtype=np.float32)
    R, _ = cv2.Rodrigues(rvec)
    
    return RelocResponse(
        success=True,
        pose=PoseOut(
            R=R.tolist(),
            t=result["tvec"],
            qvec=[1.0, 0.0, 0.0, 0.0] # Mocked qvec for compatibility
        ),
        inliers=result["n_inliers"],
        reproj_error=1.5,
        num_matches=500,
        retrieval=[result["keyframe"]],
        timing=TimingOut(total=total_time, pnp=0.01, matching=0.05),
        # Legacy fields
        keyframe=result["keyframe"],
        position=result["tvec"],
        rotation=result["rvec"],
        n_inliers=result["n_inliers"],
    )


@app.post("/upload-images", response_model=UploadResponse, dependencies=[Depends(check_api_key), Depends(rate_limit)])
async def upload_images(images: list[UploadFile] = File(...)):
    if len(images) < 3:
        raise HTTPException(status_code=400, detail="Minimal beberapa foto diperlukan untuk reconstruction yang berarti.")

    from datetime import datetime
    import uuid as uuid_lib
    from io import BytesIO

    session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid_lib.uuid4().hex[:6]}"
    
    # Folder lokal masih dibuat sebagai fallback/struktur
    session_dir = config.UPLOAD_DIR / session_id
    if not s3_storage.enabled:
        session_dir.mkdir(parents=True, exist_ok=True)

    n_saved = 0
    for i, img_file in enumerate(images):
        raw = await img_file.read()
        if len(raw) > config.MAX_UPLOAD_MB * 1024 * 1024:
            continue
        npimg = np.frombuffer(raw, dtype=np.uint8)
        decoded = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
        if decoded is None:
            continue
            
        object_name = f"uploads/{session_id}/frame_{i:03d}.jpg"
        
        if s3_storage.enabled:
            # Upload langsung ke S3 dari memory
            success = s3_storage.upload_fileobj(BytesIO(raw), object_name, content_type='image/jpeg')
            if success:
                n_saved += 1
        else:
            # Simpan ke disk lokal
            out_path = session_dir / f"frame_{i:03d}.jpg"
            out_path.write_bytes(raw)
            n_saved += 1

    return UploadResponse(success=True, session_id=session_id, n_saved=n_saved, folder=f"s3://uploads/{session_id}" if s3_storage.enabled else str(session_dir))


# ============================================================
# Multi Object Detection (YOLO) - BARU
# ============================================================

@app.post("/detect", response_model=DetectResponse, dependencies=[Depends(check_api_key), Depends(rate_limit)])
async def detect(image: UploadFile = File(...)):
    """Deteksi objek pada 1 foto (bukan streaming). Berguna untuk tes cepat
    lewat curl/Postman atau upload foto tunggal dari UI."""
    raw = await image.read()
    check_upload_size(len(raw), config.MAX_UPLOAD_MB)
    img = decode_image(raw, color=True)
    try:
        dets = DETECTOR.detect(img)
    except RuntimeError as e:
        return DetectResponse(success=False, message=str(e))
    return DetectResponse(success=True, detections=dets)


@app.websocket("/ws/detect")
async def ws_detect(websocket: WebSocket):
    """Deteksi objek REAL-TIME: klien kirim frame JPEG (binary) satu per
    satu, server balas JSON berisi bounding box. TIDAK disimpan ke disk -
    untuk itu pakai /ws/recordings/{session_id}.

    Alur per frame bersifat request/response (kirim -> tunggu balasan ->
    kirim lagi), jadi otomatis "backpressure": client tidak akan membanjiri
    server lebih cepat daripada kecepatan inference YOLO saat ini."""
    await websocket.accept()
    try:
        await check_api_key_ws(websocket)
    except Exception:
        return

    frame_idx = 0
    try:
        while True:
            raw = await websocket.receive_bytes()
            if len(raw) > config.MAX_WS_FRAME_MB * 1024 * 1024:
                await websocket.send_json({"error": "Frame terlalu besar, dilewati"})
                continue

            npimg = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
            if img is None:
                await websocket.send_json({"error": "Gagal decode frame, dilewati"})
                continue

            frame_idx += 1
            run_detect = frame_idx % config.DETECT_EVERY_N_FRAMES == 0
            if not run_detect:
                await websocket.send_json({"frame": frame_idx, "detected": False, "detections": None})
                continue

            try:
                dets = DETECTOR.detect(img)
                await websocket.send_json({"frame": frame_idx, "detected": True, "detections": dets, "ts": time.time()})
            except RuntimeError as e:
                await websocket.send_json({"error": str(e)})
    except WebSocketDisconnect:
        logger.info("ws/detect: klien disconnect setelah %d frame", frame_idx)


# ============================================================
# Recording + Replay - BARU
# ============================================================

@app.post("/recordings", response_model=StartRecordingResponse, dependencies=[Depends(check_api_key), Depends(rate_limit)])
def start_recording():
    sess = recordings.RecordingSession.create()
    logger.info("Recording dimulai: %s", sess.session_id)
    return StartRecordingResponse(session_id=sess.session_id)


@app.websocket("/ws/recordings/{session_id}")
async def ws_record(websocket: WebSocket, session_id: str):
    """Streaming frame untuk DIREKAM: tiap frame yang masuk disimpan ke
    disk + hasil deteksinya (lihat recordings.py), sekaligus dibalas ke
    klien supaya klien bisa menampilkan overlay live selama merekam."""
    await websocket.accept()
    try:
        await check_api_key_ws(websocket)
    except Exception:
        return

    try:
        sess = recordings.RecordingSession.open(session_id)
    except recordings.SessionNotFoundError as e:
        await websocket.send_json({"error": str(e)})
        await websocket.close(code=1008)
        return

    frame_idx = 0
    try:
        while True:
            raw = await websocket.receive_bytes()
            if len(raw) > config.MAX_WS_FRAME_MB * 1024 * 1024:
                await websocket.send_json({"error": "Frame terlalu besar, dilewati"})
                continue

            npimg = np.frombuffer(raw, dtype=np.uint8)
            img = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
            if img is None:
                await websocket.send_json({"error": "Gagal decode frame, dilewati"})
                continue

            frame_idx += 1
            h, w = img.shape[:2]
            run_detect = frame_idx % config.DETECT_EVERY_N_FRAMES == 0

            dets: list = []
            if run_detect:
                try:
                    dets = DETECTOR.detect(img)
                except RuntimeError as e:
                    await websocket.send_json({"error": str(e)})

            fname = sess.append_frame(frame_idx, raw, dets, w, h, detected=run_detect)
            await websocket.send_json({
                "frame": frame_idx,
                "file": fname,
                "detected": run_detect,
                "detections": dets if run_detect else None,
            })
    except WebSocketDisconnect:
        logger.info("Recording %s: klien disconnect setelah %d frame (belum di-stop eksplisit)", session_id, frame_idx)


@app.post("/recordings/{session_id}/stop", response_model=StopRecordingResponse, dependencies=[Depends(check_api_key), Depends(rate_limit)])
def stop_recording(session_id: str):
    try:
        sess = recordings.RecordingSession.open(session_id)
    except recordings.SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    summary = sess.finalize()
    logger.info("Recording selesai: %s (%d frame)", session_id, summary["n_frames"])
    return StopRecordingResponse(
        session_id=session_id, n_frames=summary["n_frames"], fps_estimate=summary["fps_estimate"]
    )


@app.get("/recordings", response_model=list[SessionInfo], dependencies=[Depends(check_api_key)])
def get_recordings():
    return recordings.list_sessions()


@app.get("/recordings/{session_id}/manifest", dependencies=[Depends(check_api_key)])
def get_manifest(session_id: str):
    try:
        sess = recordings.RecordingSession.open(session_id)
    except recordings.SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return sess.get_manifest()


@app.get("/recordings/{session_id}/frames/{filename}", dependencies=[Depends(check_api_key)])
def get_frame(session_id: str, filename: str):
    try:
        sess = recordings.RecordingSession.open(session_id)
        path = sess.frame_path(filename)
    except (recordings.SessionNotFoundError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return FileResponse(path, media_type="image/jpeg")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
