# OpenVPS — Belajar Membangun Visual Positioning System dari Nol

Proyek belajar bertahap untuk memahami cara kerja VPS komersial (seperti
MultiSet AI), dari konsep termudah sampai arsitektur edge+cloud+web.

## Struktur

| Modul | Nama | Konsep | Bahasa |
|---|---|---|---|
| 01 | `slam_mini` | Visual Odometry (dasar SLAM, pengganti ringan ORB-SLAM3/RTAB-Map) | Python |
| 02 | `colmap_mapper` | 3D reconstruction dari foto (Structure-from-Motion) | Python (pycolmap) |
| 03 | `hloc_lite` | Hierarchical localization: retrieval + matching + PnP | Python |
| 04 | `edge_vio_relay` | Server relocalization + **deteksi objek YOLO real-time** + **recording/replay** + kamera ARKit + **kamera browser** | Python + Swift + HTML/JS |

## Cara tercepat untuk mencoba (browser, tanpa install apapun di sisi kamu)

1. Deploy server (Modul 04) ke VPS - lihat `docs/deploy_to_vps.md`, atau
   jalankan lokal dulu untuk tes:
   ```bash
   cd 04_edge_vio_relay
   cp .env.example .env   # opsional untuk tes lokal - kosongkan OPENVPS_API_KEY supaya auth off
   pip install -r requirements.txt --break-system-packages
   uvicorn server:app --host 0.0.0.0 --port 8000
   ```
   Cek `http://localhost:8000/health` - kalau `detector.ready: false`, model
   YOLO belum selesai di-download/load (lihat `detector.error` untuk detail).
2. Buka `04_edge_vio_relay/web_capture.html` langsung di browser (double-click
   filenya, atau host statis manapun)
3. Masukkan alamat server di kolom "Alamat Server OpenVPS" (default
   `http://localhost:8000` untuk tes lokal), dan API Key kalau server
   production kamu mengaktifkan auth
4. Klik "Aktifkan Kamera" -> izinkan akses kamera -> ambil 10-20 foto sambil
   berkeliling ruangan/objek (overlap 60-80% antar foto) untuk bikin peta 3D,
   ATAU nyalakan toggle **"Aktifkan overlay deteksi live"** untuk lihat
   deteksi multi-objek YOLO langsung di atas video
5. Klik "Kirim Semua ke Server" -> foto tersimpan di
   `04_edge_vio_relay/collected_photos/<session_id>/` di server
6. Pakai folder itu sebagai input Modul 02:
   ```bash
   python3 02_colmap_mapper/build_map.py \
     --images 04_edge_vio_relay/collected_photos/<session_id> \
     --output 02_colmap_mapper/output_map
   ```
7. Tombol "Snap & Localize" di halaman yang sama bisa dipakai untuk tes
   endpoint `/relocalize` langsung dari browser begitu peta sudah jadi.
8. Klik **"Mulai Rekam"** untuk merekam sesi (frame + deteksi YOLO tiap
   frame) yang disimpan ke server. Klik "Berhenti Rekam" lalu "Buka Replay"
   untuk memutar ulang sesinya di `web_replay.html`, lengkap dengan bounding
   box asli tiap frame, scrubber, dan kontrol kecepatan putar.

## Deteksi Objek Real-time (YOLO) & Replay - cara kerja singkat

- **Live (tanpa simpan)**: `web_capture.html` mengirim frame kamera ke
  `WS /ws/detect` tiap ~200ms, server jalankan YOLO, balas bounding box,
  browser gambar overlay-nya di atas video. Tidak ada yang disimpan ke disk.
- **Recording (untuk replay)**: `WS /ws/recordings/{session_id}` melakukan
  hal yang sama TAPI setiap frame + hasil deteksinya juga disimpan ke
  `04_edge_vio_relay/recordings/<session_id>/`. Setelah "Berhenti Rekam",
  server merangkum sesi jadi `manifest.json`.
- **Replay**: `web_replay.html` mengambil `manifest.json` + tiap file frame
  dari server, lalu memutarnya ulang di canvas dengan bounding box digambar
  ulang dari data yang tersimpan (tidak perlu jalankan YOLO lagi saat replay
  - jadi replay tetap ringan walau server tidak sekuat itu).
- Endpoint detail ada di docstring `04_edge_vio_relay/server.py`.

## Urutan belajar lengkap

1. **01_slam_mini** — `python3 slam_mini.py --demo` — pahami dasar visual odometry
2. **04_edge_vio_relay/web_capture.html** — kumpulkan foto asli via browser,
   coba juga overlay deteksi live & recording
3. **02_colmap_mapper** — `build_map.py` dari foto yang terkumpul -> peta 3D
4. **03_hloc_lite** — pahami alur retrieve→match→localize
5. **04_edge_vio_relay/server.py** — sambungkan peta asli ke server, deploy ke VPS
6. **04_edge_vio_relay/web_replay.html** — putar ulang sesi recording

## Upgrade path ke tools production-grade

- Modul 01 -> ORB-SLAM3 asli (C++, GitHub: UZ-SLAMLab/ORB_SLAM3) kalau butuh
  akurasi lebih tinggi + loop closure + multi-map
- Modul 03 -> hloc asli (GitHub: cvg/Hierarchical-Localization) dengan
  SuperPoint+SuperGlue kalau server punya GPU
- Modul 04 -> ARCore (Android) versi dari client_arkit.swift, atau upgrade
  ke Kalman filter untuk sensor fusion VIO+server correction
- Modul 04 (deteksi) -> ganti `OPENVPS_YOLO_MODEL` ke YOLOv8s/m/l/x atau
  model custom hasil training sendiri kalau butuh akurasi lebih tinggi;
  aktifkan GPU (`OPENVPS_YOLO_DEVICE=cuda:0`) untuk latency jauh lebih rendah
- Modul 04 (recording) -> untuk skala besar/banyak sesi concurrent, ganti
  penyimpanan frame dari filesystem+jsonl ke object storage (S3-compatible)
  + database (Postgres) - lihat catatan di `04_edge_vio_relay/recordings.py`
