"""
OpenVPS - Modul tambahan: Multi Object Detection (YOLO)
==========================================================
Menambahkan deteksi objek real-time di atas pipeline VPS yang sudah ada.
Dipisah dari server.py supaya:
  - Model YOLO cuma di-load SEKALI (lazy singleton), bukan tiap request
    -> load model YOLO makan waktu beberapa detik, kalau di-load ulang tiap
       request, endpoint akan sangat lambat & CPU/GPU boros.
  - Mudah dites/diganti model tanpa sentuh kode server/websocket.

Model default: YOLOv8n (nano) dari Ultralytics - kecil (~6MB), cukup cepat
jalan di CPU VPS 1-2 vCPU untuk beberapa fps. Ganti env OPENVPS_YOLO_MODEL
ke yolov8s/m/l/x, YOLOv9/v10/v11, atau model custom (.pt hasil training
sendiri) kalau butuh akurasi lebih tinggi dan device lebih kuat (GPU).

Model otomatis di-download oleh library ultralytics saat pertama dipakai
kalau belum ada di disk (butuh akses internet saat itu saja) - untuk
production tanpa akses internet keluar, download dulu manual lalu set
OPENVPS_YOLO_MODEL ke path lokal file .pt-nya.
"""

import logging
import threading
from typing import Any

import numpy as np

import config

logger = logging.getLogger("openvps.detector")

Detection = dict[str, Any]  # {"label": str, "cls_id": int, "conf": float, "box": [x1,y1,x2,y2]}


class ObjectDetector:
    """Singleton thread-safe di sekitar model YOLO Ultralytics.

    Thread-safe karena FastAPI/uvicorn bisa menjalankan beberapa worker
    thread untuk request sinkron. `.predict()` Ultralytics sendiri aman
    dipanggil dari banyak thread selama modelnya sudah di-load (load-nya
    yang perlu dikunci)."""

    _instance: "ObjectDetector | None" = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._model = None
        self._load_lock = threading.Lock()
        self._load_error: str | None = None

    @classmethod
    def instance(cls) -> "ObjectDetector":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def warm_up(self) -> None:
        """Panggil saat startup server supaya request pertama tidak nunggu
        loading model (opsional - kalau tidak dipanggil, model tetap
        di-load otomatis lazy saat request pertama masuk)."""
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._model is not None or self._load_error is not None:
            return
        with self._load_lock:
            if self._model is not None or self._load_error is not None:
                return
            try:
                # Import lokal (bukan di top-level file): supaya server tetap
                # bisa start cepat & endpoint lain (relocalize, upload) tetap
                # jalan normal walau ultralytics/torch belum ter-install atau
                # gagal load - hanya endpoint deteksi yang kena.
                from ultralytics import YOLO

                logger.info(
                    "Loading YOLO model '%s' (device=%s)...",
                    config.YOLO_MODEL, config.YOLO_DEVICE,
                )
                self._model = YOLO(config.YOLO_MODEL)
                logger.info(
                    "YOLO model siap. Jumlah kelas: %d", len(self._model.names)
                )
            except Exception as e:  # noqa: BLE001 - sengaja tangkap semua supaya
                # error load model (file hilang, torch tidak ada, dll)
                # dilaporkan lewat /health, bukan bikin server crash total.
                self._load_error = f"Gagal load model YOLO '{config.YOLO_MODEL}': {e}"
                logger.exception(self._load_error)

    def detect(self, img_bgr: np.ndarray) -> list[Detection]:
        """Jalankan deteksi multi-objek pada 1 frame BGR (hasil cv2.imdecode).
        Raise RuntimeError kalau model gagal/belum bisa di-load."""
        self._ensure_loaded()
        if self._model is None:
            raise RuntimeError(self._load_error or "Model YOLO belum siap")

        results = self._model.predict(
            img_bgr,
            conf=config.YOLO_CONF,
            iou=config.YOLO_IOU,
            imgsz=config.YOLO_IMG_SIZE,
            max_det=config.YOLO_MAX_DET,
            device=config.YOLO_DEVICE,
            verbose=False,
        )

        detections: list[Detection] = []
        if not results:
            return detections

        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return detections

        names = r.names  # dict cls_id -> label
        for box in r.boxes:
            cls_id = int(box.cls[0])
            conf = float(box.conf[0])
            x1, y1, x2, y2 = [round(float(v), 1) for v in box.xyxy[0].tolist()]
            detections.append({
                "label": names.get(cls_id, str(cls_id)),
                "cls_id": cls_id,
                "conf": round(conf, 4),
                "box": [x1, y1, x2, y2],
            })
        return detections


def get_detector() -> ObjectDetector:
    return ObjectDetector.instance()
