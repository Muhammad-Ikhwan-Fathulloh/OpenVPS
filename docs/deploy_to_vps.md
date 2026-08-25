# Deploy OpenVPS Server ke VPS

Server yang dideploy adalah **Modul 04 (`04_edge_vio_relay/server.py`)** —
FastAPI, sekarang mencakup relocalization + **multi object detection (YOLO)**
+ **recording/replay**. Spek minimum:
- Tanpa YOLO (relocalization + capture saja): 1 vCPU / 1GB RAM cukup.
- Dengan YOLO aktif di CPU: disarankan minimal 2 vCPU / 2-4GB RAM (YOLOv8n
  masih ringan, tapi torch + inference tetap butuh headroom). Kalau ada GPU
  (mis. VPS dengan GPU passthrough), set `OPENVPS_YOLO_DEVICE=cuda:0` untuk
  latency jauh lebih rendah.

## 0. Wajib sebelum deploy: siapkan konfigurasi

```bash
cd openvps/04_edge_vio_relay
cp .env.example .env
# Generate API key:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Isi OPENVPS_API_KEY dengan hasilnya, dan OPENVPS_CORS_ORIGINS dengan
# domain frontend kamu (bukan "*") di .env
```

## Opsi A — Manual (systemd + nginx), paling gampang dipahami

```bash
# 1. Di VPS (Ubuntu 22.04/24.04)
sudo apt update && sudo apt install -y python3-pip python3-venv nginx

# 2. Clone/upload project
git clone <repo-kamu> openvps && cd openvps

# 3. Setup virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -r 04_edge_vio_relay/requirements.txt
# (kalau tidak butuh deteksi objek sama sekali, hapus baris "ultralytics"
#  dari requirements.txt dulu supaya instalasi lebih cepat & ringan)

# 4. Jalankan via systemd (auto-restart kalau crash/reboot)
sudo tee /etc/systemd/system/openvps.service <<EOF
[Unit]
Description=OpenVPS Relocalization + Detection Server
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/ke/openvps/04_edge_vio_relay
EnvironmentFile=/path/ke/openvps/04_edge_vio_relay/.env
ExecStart=/path/ke/openvps/venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now openvps
sudo systemctl status openvps   # pastikan "active (running)", cek log kalau tidak

# 5. Reverse proxy nginx + HTTPS (wajib, karena ARKit/ARCore butuh HTTPS,
#    dan WebSocket /ws/detect & /ws/recordings butuh proxy yang benar)
sudo tee /etc/nginx/sites-available/openvps <<EOF
server {
    listen 80;
    server_name vps-kamu.example.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;   # wajib untuk WebSocket
        proxy_set_header Connection "upgrade";      # wajib untuk WebSocket
        proxy_set_header Host \$host;
        proxy_read_timeout 3600s;                   # WS live streaming, jangan timeout cepat
        client_max_body_size 20M;   # foto/frame bisa beberapa MB
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/openvps /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 6. HTTPS gratis via certbot (juga otomatis upgrade ws:// -> wss:// di sisi browser)
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d vps-kamu.example.com
```

## Opsi B — Docker (lebih portable, gampang dipindah antar VPS)

`Dockerfile` sudah tersedia di `04_edge_vio_relay/Dockerfile`. Build context-nya
harus dari ROOT project (bukan dari dalam folder 04_edge_vio_relay), karena
image butuh folder `03_hloc_lite` juga:

```bash
cd openvps   # root project, bukan 04_edge_vio_relay
docker build -t openvps-server -f 04_edge_vio_relay/Dockerfile .
docker run -d --restart always \
  --env-file 04_edge_vio_relay/.env \
  -v $(pwd)/04_edge_vio_relay/recordings:/app/04_edge_vio_relay/recordings \
  -v $(pwd)/04_edge_vio_relay/collected_photos:/app/04_edge_vio_relay/collected_photos \
  -p 127.0.0.1:8000:8000 --name openvps openvps-server
# lalu nginx + certbot sama seperti Opsi A langkah 5-6
```

Volume di atas (`-v`) penting supaya foto capture & recording session TIDAK
hilang saat container di-restart/redeploy.

## Model YOLO di lingkungan tanpa akses internet keluar

`ultralytics` otomatis download model (mis. `yolov8n.pt`) saat pertama kali
dipakai. Kalau VPS produksi tidak punya akses internet keluar (network
policy ketat), download dulu model-nya di mesin lain, taruh file `.pt`-nya
di `04_edge_vio_relay/`, lalu set `OPENVPS_YOLO_MODEL=/app/04_edge_vio_relay/yolov8n.pt`
(sesuaikan path) di `.env`.

## Checklist sebelum production

- [ ] Ganti `build_demo_map()` di `server.py` dengan peta ASLI hasil Modul 02
      (COLMAP) + Modul 03 (build map database dari titik 3D COLMAP, bukan data
      sintetis) — set `OPENVPS_MAP_PATH` dan lengkapi `MapDatabase.load()` di
      `03_hloc_lite/hloc_lite.py` (saat ini baru ada `save()`, belum `load()`)
- [ ] HTTPS wajib (ARKit/ARCore App Transport Security menolak HTTP polos, dan
      browser modern menolak akses kamera di halaman non-HTTPS selain localhost)
- [ ] `OPENVPS_API_KEY` **wajib diisi** — tanpa ini semua endpoint termasuk
      `/recordings/*` (bisa berisi footage kamera) bisa diakses siapa saja
- [ ] `OPENVPS_CORS_ORIGINS` diisi domain frontend asli, jangan biarkan `"*"`
- [ ] nginx `client_max_body_size` disesuaikan ukuran foto rata-rata device,
      dan proxy WebSocket (`Upgrade`/`Connection` header) sudah benar - tanpa
      ini `/ws/detect` & `/ws/recordings` akan gagal konek di balik nginx
- [ ] Kalau peta makin besar (banyak keyframe), ganti retrieval histogram
      dengan FAISS/vector DB supaya tetap cepat di ribuan keyframe
- [ ] Kalau CPU server terbatas, set `OPENVPS_DETECT_EVERY_N` > 1 supaya YOLO
      tidak jalan di setiap frame (frame tetap tersimpan utuh untuk replay,
      hanya deteksinya yang di-skip sebagian)
- [ ] Rotasi/bersihkan folder `recordings/` & `collected_photos/` secara
      berkala (mis. cron) - keduanya menyimpan file gambar mentah dan bisa
      cepat memenuhi disk kalau dipakai terus-menerus
- [ ] Monitoring: log `n_inliers` tiap request relocalize - kalau turun terus
      artinya peta sudah "basi" (lingkungan berubah) dan perlu re-scan
- [ ] Backup rutin `OPENVPS_MAP_PATH` (peta hasil scan biasanya susah/lama
      dibuat ulang)
