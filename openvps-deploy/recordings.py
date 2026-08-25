"""
OpenVPS - Modul tambahan: Recording & Replay
================================================
Menyimpan tiap frame yang di-stream client (live capture) beserta hasil
deteksi YOLO-nya ke disk, supaya sesi capture bisa DI-REPLAY nanti persis
seperti aslinya (frame asli + bounding box per frame), tanpa perlu jalankan
YOLO ulang tiap kali diputar.

Struktur di disk:
  recordings/<session_id>/
    meta.json           -> info sesi (dibuat saat start)
    manifest.jsonl       -> 1 baris JSON per frame (append-only SELAMA recording)
    manifest.json         -> ringkasan final, dibuat sekali saat stop/finalize
    frames/frame_000001.jpg, frame_000002.jpg, ...

Kenapa jsonl saat recording, bukan langsung json? Supaya kalau server
crash/koneksi client putus di tengah recording, frame yang SUDAH masuk
tetap tersimpan dan tidak hilang semua (append-only, bukan rewrite tiap
frame - rewrite tiap frame juga O(n) dan makin lambat seiring sesi memanjang).

Catatan skala: implementasi ini pakai filesystem + jsonl supaya proyek
belajar ini tetap mudah dibaca tanpa dependency DB. Untuk production dengan
banyak sesi/replay concurrent, ganti ke object storage (S3-compatible) untuk
frame + database (Postgres/SQLite) untuk manifest.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import config


class SessionNotFoundError(Exception):
    pass


class RecordingSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.dir = config.RECORDINGS_DIR / session_id
        self.frames_dir = self.dir / "frames"
        self.manifest_jsonl = self.dir / "manifest.jsonl"
        self.meta_path = self.dir / "meta.json"
        self.summary_path = self.dir / "manifest.json"

    # --- lifecycle ---

    @classmethod
    def create(cls) -> "RecordingSession":
        session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        sess = cls(session_id)
        sess.frames_dir.mkdir(parents=True, exist_ok=True)
        sess.meta_path.write_text(json.dumps({
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "recording",
        }, indent=2))
        return sess

    @classmethod
    def open(cls, session_id: str) -> "RecordingSession":
        # Cegah path traversal lewat session_id yang aneh-aneh (mis. "../../etc")
        if session_id != Path(session_id).name:
            raise SessionNotFoundError(f"Session id tidak valid: {session_id}")
        sess = cls(session_id)
        if not sess.dir.exists():
            raise SessionNotFoundError(f"Sesi recording '{session_id}' tidak ditemukan")
        return sess

    # --- recording ---

    def append_frame(
        self, frame_idx: int, jpg_bytes: bytes, detections: list, w: int, h: int,
        detected: bool,
    ) -> str:
        """Simpan 1 frame + hasil deteksinya. `detected=False` berarti frame
        ini di-skip dari YOLO (lihat config.DETECT_EVERY_N_FRAMES) - disimpan
        tetap sebagai None supaya player replay tahu harus 'menahan' box
        terakhir, bukan menganggap tidak ada objek terdeteksi."""
        fname = f"frame_{frame_idx:06d}.jpg"
        (self.frames_dir / fname).write_bytes(jpg_bytes)
        record = {
            "frame": frame_idx,
            "file": fname,
            "ts": time.time(),
            "w": w,
            "h": h,
            "detected": detected,
            "detections": detections if detected else None,
        }
        with self.manifest_jsonl.open("a") as f:
            f.write(json.dumps(record) + "\n")
        return fname

    def _read_jsonl(self) -> list[dict]:
        if not self.manifest_jsonl.exists():
            return []
        records = []
        with self.manifest_jsonl.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # baris korup (mis. server mati saat menulis) - skip, jangan gagalkan semua
        return records

    def finalize(self) -> dict:
        """Dipanggil saat client berhenti merekam. Membangun manifest.json
        final (ringkasan + estimasi fps) dari manifest.jsonl."""
        records = self._read_jsonl()
        meta = json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}

        fps_estimate = None
        if len(records) >= 2:
            duration = records[-1]["ts"] - records[0]["ts"]
            if duration > 0:
                fps_estimate = round((len(records) - 1) / duration, 2)

        summary = {
            "session_id": self.session_id,
            "created_at": meta.get("created_at"),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "done",
            "n_frames": len(records),
            "fps_estimate": fps_estimate,
            "frames": records,
        }
        self.summary_path.write_text(json.dumps(summary, indent=2))
        meta["status"] = "done"
        meta["n_frames"] = len(records)
        self.meta_path.write_text(json.dumps(meta, indent=2))
        return summary

    # --- reading / replay ---

    def get_manifest(self) -> dict:
        if self.summary_path.exists():
            return json.loads(self.summary_path.read_text())
        # Belum di-finalize (masih recording, atau client putus tanpa stop) ->
        # bangun on-the-fly dari jsonl supaya tetap bisa di-replay sebagian.
        records = self._read_jsonl()
        meta = json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}
        return {
            "session_id": self.session_id,
            "created_at": meta.get("created_at"),
            "status": meta.get("status", "recording"),
            "n_frames": len(records),
            "fps_estimate": None,
            "frames": records,
        }

    def frame_path(self, filename: str) -> Path:
        # Cegah path traversal: hanya izinkan nama file polos di dalam frames_dir.
        safe_name = Path(filename).name
        p = (self.frames_dir / safe_name).resolve()
        if not p.is_relative_to(self.frames_dir.resolve()) or not p.exists():
            raise FileNotFoundError(f"Frame '{filename}' tidak ditemukan")
        return p


def list_sessions() -> list[dict]:
    out = []
    if not config.RECORDINGS_DIR.exists():
        return out
    for d in sorted(config.RECORDINGS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        n_frames = meta.get("n_frames")
        if n_frames is None:
            jsonl = d / "manifest.jsonl"
            n_frames = 0
            if jsonl.exists():
                with jsonl.open() as f:
                    n_frames = sum(1 for line in f if line.strip())
        out.append({
            "session_id": meta.get("session_id", d.name),
            "created_at": meta.get("created_at"),
            "status": meta.get("status", "unknown"),
            "n_frames": n_frames,
        })
    return out
