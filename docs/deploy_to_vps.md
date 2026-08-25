# Deploy OpenVPS Server ke VPS

Server yang dideploy adalah **Modul 04 (`04_edge_vio_relay/server.py`)** —
FastAPI, ringan, cukup 1 vCPU / 1GB RAM untuk mulai (naikkan kalau peta besar
atau traffic tinggi).

## Opsi A — Manual (systemd + nginx), paling gampang dipahami

```bash
# 1. Di VPS (Ubuntu 22.04/24.04)
sudo apt update && sudo apt install -y python3-pip python3-venv nginx

# 2. Clone/upload project
git clone <repo-kamu> openvps && cd openvps

# 3. Setup virtualenv
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn python-multipart opencv-python-headless numpy pycolmap

# 4. Jalankan via systemd (auto-restart kalau crash/reboot)
sudo tee /etc/systemd/system/openvps.service <<EOF
[Unit]
Description=OpenVPS Relocalization Server
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/ke/openvps/04_edge_vio_relay
ExecStart=/path/ke/openvps/venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now openvps

# 5. Reverse proxy nginx + HTTPS (wajib, karena ARKit/ARCore butuh HTTPS)
sudo tee /etc/nginx/sites-available/openvps <<EOF
server {
    listen 80;
    server_name vps-kamu.example.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        client_max_body_size 20M;   # foto bisa beberapa MB
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/openvps /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 6. HTTPS gratis via certbot
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d vps-kamu.example.com
```

## Opsi B — Docker (lebih portable, gampang dipindah antar VPS)

```dockerfile
# openvps/04_edge_vio_relay/Dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY ../03_hloc_lite /app/03_hloc_lite
COPY . /app/04_edge_vio_relay
WORKDIR /app/04_edge_vio_relay
RUN pip install fastapi uvicorn python-multipart opencv-python-headless numpy
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker build -t openvps-server -f 04_edge_vio_relay/Dockerfile .
docker run -d --restart always -p 127.0.0.1:8000:8000 --name openvps openvps-server
# lalu nginx + certbot sama seperti Opsi A langkah 5-6
```

## Checklist sebelum production

- [ ] Ganti `build_demo_map()` di `server.py` dengan peta ASLI hasil Modul 02
      (COLMAP) + Modul 03 (build map database dari titik 3D COLMAP, bukan data sintetis)
- [ ] HTTPS wajib (ARKit/ARCore App Transport Security menolak HTTP polos)
- [ ] Batasi `client_max_body_size` nginx sesuai ukuran foto rata-rata device
- [ ] Tambah autentikasi (API key/token) di endpoint `/relocalize` supaya
      tidak dipakai sembarang orang
- [ ] Kalau peta makin besar (banyak keyframe), ganti retrieval histogram
      dengan FAISS/vector DB supaya tetap cepat di ribuan keyframe
- [ ] Monitoring: log `n_inliers` tiap request - kalau turun terus artinya
      peta sudah "basi" (lingkungan berubah) dan perlu re-scan
