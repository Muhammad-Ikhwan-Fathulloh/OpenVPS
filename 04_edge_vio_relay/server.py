"""
OpenVPS - Module 04: Edge VIO + Server Relocalization
==========================================================
Konsep: HP (ARKit/ARCore) sudah punya VIO on-device yang bagus untuk gerakan
JANGKA PENDEK (halus, real-time, low-latency) tapi driftnya menumpuk lama-
lama (posisi makin meleset seiring waktu, karena tidak ada referensi
absolut). MultiSet AI dkk menambahkan "server-side relocalization": setiap
beberapa detik, HP kirim 1 foto ke server, server localize foto itu
terhadap PETA GLOBAL (hasil Modul 02+03), lalu KOREKSI posisi VIO yang sudah
drift itu supaya balik akurat.

  [HP: ARKit/ARCore VIO]  --terus-menerus (60fps, on-device, halus)--> pose lokal
         |
         |--setiap N detik, kirim 1 foto--> [SERVER: hloc-lite]
         |                                         |
         |<--pose koreksi (absolut, akurat)--------|
         |
  [HP: gabungkan pose VIO + koreksi server -> pose final stabil & akurat]

File ini adalah SERVER-nya (Python, FastAPI - ringan, gampang deploy ke VPS).
Kode client ARKit ada di client_arkit.swift di folder yang sama.

DEPLOY KE VPS (di akhir dokumen ini juga ada instruksi Docker):
  pip install fastapi uvicorn python-multipart --break-system-packages
  uvicorn server:app --host 0.0.0.0 --port 8000

ENDPOINT:
  GET  /health          -> cek server hidup + jumlah keyframe di peta
  POST /relocalize      -> kirim 1 foto -> dapat pose absolut hasil localize
  POST /upload-images   -> kirim banyak foto sekaligus (dari web_capture.html)
                            -> disimpan ke disk, siap diproses Modul 02 (COLMAP)
"""

import io
import sys
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import cv2
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.append(str(Path(__file__).resolve().parents[1] / "03_hloc_lite"))
from hloc_lite import MapDatabase, localize_query, build_demo_map  # noqa: E402

app = FastAPI(title="OpenVPS Relocalization Server")

# CORS diaktifkan supaya halaman web (web_capture.html) yang dibuka dari
# domain/file manapun tetap bisa memanggil server ini dari browser.
# Di production, ganti allow_origins ke domain frontend kamu saja.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Peta di-load sekali saat server start (di production: load hasil Modul 02+03
# yang sudah dibangun dari scan asli, bukan build_demo_map())
MAP_DB: MapDatabase = build_demo_map()

UPLOAD_DIR = Path(__file__).resolve().parent / "collected_photos"
UPLOAD_DIR.mkdir(exist_ok=True)


class RelocResponse(BaseModel):
    success: bool
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


@app.get("/health")
def health():
    return {"status": "ok", "n_keyframes": len(MAP_DB.keyframes)}


@app.post("/relocalize", response_model=RelocResponse)
async def relocalize(image: UploadFile = File(...)):
    """
    Endpoint utama: HP kirim 1 foto -> server balas pose absolut hasil
    localize terhadap peta global.
    """
    raw = await image.read()
    npimg = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(npimg, cv2.IMREAD_GRAYSCALE)

    if img is None:
        raise HTTPException(status_code=400, detail="Gagal decode gambar")

    try:
        result = localize_query(MAP_DB, img)
    except RuntimeError as e:
        return RelocResponse(success=False, message=str(e))

    return RelocResponse(
        success=True,
        keyframe=result["keyframe"],
        position=result["tvec"],
        rotation=result["rvec"],
        n_inliers=result["n_inliers"],
    )


@app.post("/upload-images", response_model=UploadResponse)
async def upload_images(images: list[UploadFile] = File(...)):
    """
    Endpoint untuk web_capture.html: terima 10-20 foto sekaligus dari
    kamera browser, simpan ke disk dengan nama file berurutan.

    Folder hasilnya (collected_photos/<session_id>/) langsung bisa dipakai
    sebagai --images di Modul 02 (build_map.py) untuk bikin peta 3D:

        python3 ../02_colmap_mapper/build_map.py \\
            --images collected_photos/<session_id> \\
            --output ../02_colmap_mapper/output_map
    """
    if len(images) < 3:
        raise HTTPException(
            status_code=400,
            detail="Minimal beberapa foto diperlukan untuk reconstruction yang berarti."
        )

    session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    n_saved = 0
    for i, img_file in enumerate(images):
        raw = await img_file.read()
        # Validasi: pastikan memang gambar yang bisa didecode, bukan file sampah
        npimg = np.frombuffer(raw, dtype=np.uint8)
        decoded = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
        if decoded is None:
            continue
        out_path = session_dir / f"frame_{i:03d}.jpg"
        out_path.write_bytes(raw)
        n_saved += 1

    return UploadResponse(
        success=True,
        session_id=session_id,
        n_saved=n_saved,
        folder=str(session_dir),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
