# OpenVPS — Belajar Membangun Visual Positioning System dari Nol

Proyek belajar bertahap untuk memahami cara kerja VPS komersial (seperti
MultiSet AI), dari konsep termudah sampai arsitektur edge+cloud+web lengkap
dengan CI/CD otomatis ke Docker Hub.

---

## Struktur Modul

| Modul | Folder | Konsep | Bahasa |
|---|---|---|---|
| 01 | `01_slam_mini` | Visual Odometry — dasar SLAM, pengganti ringan ORB-SLAM3 | Python |
| 02 | `02_colmap_mapper` | 3D Reconstruction dari foto (Structure-from-Motion) | Python (pycolmap) |
| 03 | `03_hloc_lite` | Hierarchical Localization: retrieval + matching + PnP | Python |
| 04 | `04_edge_vio_relay` | Server relocalization + deteksi objek YOLO real-time + recording/replay + kamera browser | Python + HTML/JS |
| — | `openvps-deploy` | Paket deploy production: Dockerfile, docker-compose, CI/CD | Docker / GitHub Actions |

---

## Cara Cepat Mencoba (browser, tanpa install apa pun)

1. **Jalankan server lokal:**
   ```bash
   cd 04_edge_vio_relay
   cp .env.example .env   # kosongkan OPENVPS_API_KEY supaya auth off untuk tes
   pip install -r requirements.txt
   uvicorn server:app --host 0.0.0.0 --port 8000
   ```
   Cek `http://localhost:8000/health`. Kalau `detector.ready: false`, model
   YOLO masih didownload — tunggu sebentar dan cek `detector.error` untuk detail.

2. Buka `04_edge_vio_relay/web_capture.html` langsung di browser (double-click).

3. Masukkan alamat server (`http://localhost:8000`) dan API Key kalau diaktifkan.

4. Klik **"Aktifkan Kamera"** → izinkan akses kamera → ambil 10–20 foto sambil
   berkeliling ruangan (overlap 60–80% antar foto) untuk membuat peta 3D, ATAU
   nyalakan toggle **"Aktifkan overlay deteksi live"** untuk melihat deteksi
   YOLO langsung di atas video.

5. Klik **"Kirim Semua ke Server"** → foto tersimpan di
   `04_edge_vio_relay/collected_photos/<session_id>/`.

6. Gunakan folder itu sebagai input Modul 02:
   ```bash
   python3 02_colmap_mapper/build_map.py \
     --images 04_edge_vio_relay/collected_photos/<session_id> \
     --output 02_colmap_mapper/output_map
   ```

7. **"Snap & Localize"** di halaman yang sama bisa dipakai untuk tes endpoint
   `/relocalize` dari browser setelah peta jadi.

8. Klik **"Mulai Rekam"** untuk merekam sesi (frame + deteksi YOLO tiap frame).
   Klik "Berhenti Rekam" lalu "Buka Replay" untuk memutar ulang di
   `web_replay.html` lengkap dengan bounding box, scrubber, dan kontrol kecepatan.

---

## Deteksi Objek Real-time (YOLO) & Replay

- **Live (tanpa simpan):** `web_capture.html` mengirim frame ke `WS /ws/detect`
  tiap ~200ms, server jalankan YOLO, balas bounding box, browser gambar overlay
  di atas video. Tidak ada yang disimpan ke disk.

- **Recording:** `WS /ws/recordings/{session_id}` melakukan hal yang sama tapi
  setiap frame + hasil deteksi juga disimpan ke
  `04_edge_vio_relay/recordings/<session_id>/`. Setelah stop, server buat
  `manifest.json`.

- **Replay:** `web_replay.html` ambil `manifest.json` + tiap frame dari server,
  putar ulang di canvas dengan bounding box dari data tersimpan — tidak perlu
  YOLO lagi saat replay.

---

## CI/CD — Build & Push ke Docker Hub

Setiap push ke branch `main` yang menyentuh folder `openvps-deploy/` akan
secara otomatis membangun Docker image dan mendorongnya ke Docker Hub.

### Cara Kerja

```
push ke main
    │
    └─► GitHub Actions (.github/workflows/deploy.yml)
            │
            ├─ Checkout repo
            ├─ Docker Buildx setup
            ├─ Login ke Docker Hub
            ├─ Build image dari ./openvps-deploy (context + Dockerfile)
            └─ Push ke Docker Hub
                  ├─ tag: latest
                  └─ tag: sha-<short commit hash>
```

Workflow hanya trigger kalau ada perubahan di `openvps-deploy/**` — perubahan
di modul lain (01, 02, 03) tidak akan memicu build Docker.

### Setup Secrets GitHub (wajib sebelum workflow bisa jalan)

Buka **Settings → Secrets and variables → Actions** di repository GitHub, lalu
tambahkan dua secrets berikut:

| Secret | Nilai |
|---|---|
| `DOCKERHUB_USERNAME` | Username Docker Hub kamu |
| `DOCKERHUB_TOKEN` | Access Token Docker Hub (bukan password) |

Cara buat token Docker Hub:
[hub.docker.com → Account Settings → Personal access tokens → Generate new token](https://hub.docker.com/settings/security)
— pilih permission **Read & Write**.

### Trigger Manual

Workflow juga bisa dijalankan manual tanpa push lewat tab **Actions → Build &
Push to Docker Hub → Run workflow** di GitHub.

---

## Deploy ke VPS

### Setup Awal (sekali saja)

1. **Upload file ke VPS:**
   ```bash
   scp openvps-deploy/docker-compose.prod.yml openvps-deploy/.env.example user@vps:~/openvps/
   # ATAU clone repo langsung di VPS (hanya ambil file yang dibutuhkan)
   ```

2. **Buat file `.env` di VPS:**
   ```bash
   cd ~/openvps
   cp .env.example .env
   # Edit .env — minimal isi:
   #   DOCKERHUB_USERNAME=username_kamu
   #   OPENVPS_API_KEY=<random string min 32 karakter>
   # Generate API key:
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

3. **Jalankan dengan script otomatis:**
   ```bash
   # Upload deploy.sh ke VPS dulu (dari openvps-deploy/)
   chmod +x deploy.sh && ./deploy.sh
   ```
   Script ini akan: bersihkan Docker lama → cek disk → pull image dari Docker
   Hub → jalankan container.

   Atau manual tanpa script:
   ```bash
   docker compose -f docker-compose.prod.yml pull
   docker compose -f docker-compose.prod.yml up -d
   ```

### Update Setelah Deploy Awal

Cukup push perubahan ke `main` — GitHub Actions build otomatis dan push image
baru ke Docker Hub. Untuk pull image terbaru di VPS:

```bash
cd ~/openvps
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

Atau otomasi dengan [Watchtower](https://containrrr.dev/watchtower/) supaya VPS
pull sendiri setiap ada image baru.

### Nginx + HTTPS (wajib untuk production)

ARKit/ARCore dan browser modern menolak akses kamera di halaman non-HTTPS
selain localhost. WebSocket (`/ws/detect`, `/ws/recordings`) juga butuh header
proxy yang benar.

```nginx
server {
    listen 80;
    server_name domain-kamu.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;    # wajib WebSocket
        proxy_set_header Connection "upgrade";     # wajib WebSocket
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
        client_max_body_size 20M;
    }
}
```

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d domain-kamu.com
```

### Variabel Environment Penting

| Variabel | Default | Keterangan |
|---|---|---|
| `OPENVPS_API_KEY` | _(kosong)_ | **Wajib diisi di production.** Kosong = auth off |
| `OPENVPS_CORS_ORIGINS` | `*` | Domain frontend. Ganti ke domain spesifik di production |
| `OPENVPS_YOLO_MODEL` | `yolov8n.pt` | Model YOLO. Ganti ke `yolov8s/m/l/x` untuk akurasi lebih tinggi |
| `OPENVPS_YOLO_DEVICE` | `cpu` | Ganti ke `cuda:0` kalau VPS punya GPU |
| `OPENVPS_DETECT_EVERY_N` | `1` | Naikkan ke 2–4 kalau CPU terbatas |
| `DOCKERHUB_USERNAME` | — | Dipakai `docker-compose.prod.yml` untuk nama image |

Lihat `.env.example` untuk daftar lengkap.

---

## Urutan Belajar Lengkap

1. **`01_slam_mini`** — `python3 slam_mini.py --demo` — pahami dasar visual odometry
2. **`04_edge_vio_relay/web_capture.html`** — kumpulkan foto asli via browser,
   coba overlay deteksi live & recording
3. **`02_colmap_mapper`** — `build_map.py` dari foto yang terkumpul → peta 3D
4. **`03_hloc_lite`** — pahami alur retrieve → match → localize
5. **`04_edge_vio_relay/server.py`** — sambungkan peta asli ke server, deploy ke VPS
6. **`04_edge_vio_relay/web_replay.html`** — putar ulang sesi recording

---

## Checklist Production

- [ ] Ganti `build_demo_map()` di `server.py` dengan peta ASLI dari Modul 02 + 03
- [ ] `OPENVPS_API_KEY` **wajib diisi** — semua endpoint termasuk `/recordings/*`
      bisa diakses siapa saja kalau kosong
- [ ] `OPENVPS_CORS_ORIGINS` diisi domain spesifik, bukan `*`
- [ ] HTTPS aktif — ARKit/ARCore dan browser modern wajib HTTPS untuk akses kamera
- [ ] Nginx dikonfigurasi dengan header WebSocket (`Upgrade` + `Connection`)
- [ ] Docker Hub secrets (`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`) sudah diset di
      GitHub repository settings
- [ ] Rotasi folder `recordings/` & `collected_photos/` secara berkala (cron)
      supaya disk tidak penuh
- [ ] Backup rutin file peta di `OPENVPS_MAP_PATH` — susah dibuat ulang kalau hilang
- [ ] Monitor `n_inliers` tiap request relocalize — kalau turun terus, peta perlu
      di-scan ulang karena lingkungan sudah berubah

---

## Upgrade Path ke Tools Production-Grade

| Modul | Sekarang | Upgrade |
|---|---|---|
| 01 | Visual odometry sederhana | [ORB-SLAM3](https://github.com/UZ-SLAMLab/ORB_SLAM3) untuk loop closure + multi-map |
| 03 | hloc-lite (simplified) | [hloc asli](https://github.com/cvg/Hierarchical-Localization) dengan SuperPoint+SuperGlue (butuh GPU) |
| 04 (deteksi) | YOLOv8n CPU | Ganti model ke YOLOv8s/m/l/x atau model custom; aktifkan `OPENVPS_YOLO_DEVICE=cuda:0` |
| 04 (storage) | Filesystem + JSONL | S3-compatible object storage + PostgreSQL untuk skala banyak sesi concurrent |
| 04 (client) | Web browser | Native ARKit (iOS) atau ARCore (Android) untuk sensor fusion VIO + server correction |
