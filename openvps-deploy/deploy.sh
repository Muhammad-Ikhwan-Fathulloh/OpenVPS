#!/bin/bash
# =============================================================
#  OpenVPS - VPS Setup Script (first-time / manual redeploy)
#  Untuk deploy rutin, gunakan GitHub Actions (push ke main).
#
#  Cara pakai pertama kali:
#    1. Upload deploy.sh + docker-compose.prod.yml + .env ke VPS
#    2. chmod +x deploy.sh && ./deploy.sh
# =============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}==============================${NC}"
echo -e "${YELLOW}  OpenVPS VPS Deploy Script   ${NC}"
echo -e "${YELLOW}==============================${NC}"

# Cek file yang dibutuhkan
if [ ! -f "docker-compose.prod.yml" ]; then
    echo -e "${RED}ERROR: docker-compose.prod.yml tidak ditemukan di folder ini.${NC}"
    exit 1
fi
if [ ! -f ".env" ]; then
    echo -e "${RED}ERROR: .env tidak ditemukan. Copy dari .env.example lalu isi nilainya.${NC}"
    exit 1
fi

# ── 1. Cek disk ───────────────────────────────────────────
echo -e "\n${YELLOW}[1/5] Disk usage sekarang:${NC}"
df -h /

# ── 2. Bersihkan sisa Docker lama ────────────────────────
echo -e "\n${YELLOW}[2/5] Bersihkan Docker lama...${NC}"
docker compose -f docker-compose.prod.yml down --remove-orphans 2>/dev/null || true
docker rm -f openvps-app 2>/dev/null || true
docker volume prune -f
docker image prune -a -f
docker builder prune -a -f

# ── 3. Cek disk setelah cleanup ───────────────────────────
echo -e "\n${YELLOW}[3/5] Disk setelah cleanup:${NC}"
df -h /

FREE_KB=$(df / | awk 'NR==2 {print $4}')
if [ "$FREE_KB" -lt 2097152 ]; then  # kurang dari 2GB
    FREE_GB=$(echo "scale=1; $FREE_KB / 1048576" | bc)
    echo -e "${RED}⚠️  WARNING: Hanya tersisa ${FREE_GB}GB. Minimal 2GB dibutuhkan.${NC}"
    du -sh /* 2>/dev/null | sort -rh | head -10
    read -p "Lanjut anyway? (y/N): " confirm
    [[ "$confirm" != "y" && "$confirm" != "Y" ]] && exit 1
fi

# ── 4. Pull image dari Docker Hub ────────────────────────
echo -e "\n${YELLOW}[4/5] Pull image dari Docker Hub...${NC}"
# Baca DOCKERHUB_USERNAME dari .env kalau ada
if grep -q "DOCKERHUB_USERNAME" .env 2>/dev/null; then
    export $(grep "DOCKERHUB_USERNAME" .env | xargs)
fi
docker compose -f docker-compose.prod.yml pull

# ── 5. Jalankan ──────────────────────────────────────────
echo -e "\n${YELLOW}[5/5] Jalankan OpenVPS...${NC}"
docker compose -f docker-compose.prod.yml up -d

echo -e "\n${GREEN}✅ Deploy selesai!${NC}"
echo ""
docker compose -f docker-compose.prod.yml ps
echo ""
echo "Log realtime  : docker compose -f docker-compose.prod.yml logs -f"
echo "Health check  : curl http://127.0.0.1:8000/health"
