"""
OpenVPS - Module 02: COLMAP Mapper
=====================================
Ini fondasi "MAPPING" di VPS sungguhan (persis yang dipakai MultiSet untuk
bikin peta dari scan). COLMAP melakukan Structure-from-Motion (SfM):

  kumpulan foto -> deteksi fitur di tiap foto -> cocokkan fitur antar semua
  pasangan foto -> rekonstruksi posisi kamera tiap foto + titik 3D di dunia
  nyata (sparse point cloud) -> ini menjadi "peta" untuk localization nanti

INSTALL:
  pip install pycolmap --break-system-packages

CARA PAKAI:
  1. Siapkan folder foto (minimal 10-20 foto, overlap 60-80% antar foto,
     dari sudut yang berbeda-beda mengelilingi objek/ruangan)
  2. python3 build_map.py --images path/to/photos --output path/to/map_output

HASIL:
  - map_output/database.db  -> database fitur & kecocokan
  - map_output/sparse/0/    -> model 3D: kamera + titik 3D (cameras.bin,
                                images.bin, points3D.bin)
  Model ini yang nanti dipakai Modul 03 (hloc-lite) untuk localize foto baru.

CATATAN:
  Reconstruction butuh foto ASLI dengan overlap visual yang cukup. Foto
  sintetis/random TIDAK akan berhasil rekonstruksi (tidak ada fitur nyata
  untuk dicocokkan) - itu sebabnya modul ini perlu foto sungguhan, beda
  dengan modul 01 & demo sebelumnya yang bisa disimulasikan.
"""

import argparse
import shutil
from pathlib import Path

import pycolmap


def build_map(image_dir: str, output_dir: str, camera_model: str = "SIMPLE_RADIAL"):
    image_dir = Path(image_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    db_path = output_dir / "database.db"
    sparse_dir = output_dir / "sparse"
    sparse_dir.mkdir(exist_ok=True)

    if db_path.exists():
        db_path.unlink()

    n_images = len(list(image_dir.glob("*.jpg"))) + len(list(image_dir.glob("*.png")))
    if n_images < 3:
        raise ValueError(
            f"Cuma ada {n_images} foto di {image_dir}. Minimal butuh beberapa "
            "foto asli dengan overlap visual untuk SfM berhasil."
        )

    print(f"[1/4] Extracting fitur visual dari {n_images} foto...")
    pycolmap.extract_features(
        database_path=str(db_path),
        image_path=str(image_dir),
        camera_model=camera_model,
    )

    print("[2/4] Mencocokkan fitur antar semua pasangan foto (exhaustive matching)...")
    pycolmap.match_exhaustive(database_path=str(db_path))

    print("[3/4] Menjalankan incremental Structure-from-Motion (bundle adjustment)...")
    maps = pycolmap.incremental_mapping(
        database_path=str(db_path),
        image_path=str(image_dir),
        output_path=str(sparse_dir),
    )

    if not maps:
        raise RuntimeError(
            "Reconstruction gagal - kemungkinan foto tidak cukup overlap "
            "atau kurang variasi sudut pandang."
        )

    print("[4/4] Selesai. Ringkasan peta:")
    for idx, recon in maps.items():
        print(f"  Model #{idx}: {recon.num_reg_images()} kamera terdaftar, "
              f"{recon.num_points3D()} titik 3D")
        model_path = sparse_dir / str(idx)
        print(f"  -> disimpan di: {model_path}")

    return maps


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True, help="folder berisi foto-foto")
    ap.add_argument("--output", required=True, help="folder output peta")
    args = ap.parse_args()
    build_map(args.images, args.output)
