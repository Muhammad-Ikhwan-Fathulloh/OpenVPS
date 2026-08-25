"""
OpenVPS - Module 03: hloc-lite
=================================
Versi ringan dari pipeline hloc (Hierarchical Localization, CVG ETH Zurich)
https://github.com/cvg/Hierarchical-Localization

Pipeline hloc asli (production-grade, dipakai konsepnya oleh VPS komersial):
  1. Global descriptor tiap gambar peta (NetVLAD) -> untuk RETRIEVAL cepat
  2. Saat query: hitung global descriptor foto baru -> cari top-K gambar
     peta paling mirip (skip pencarian ke SELURUH peta -> jadi scalable)
  3. Local feature matching (SuperPoint+SuperGlue) HANYA ke top-K gambar itu
  4. PnP dari kecocokan 2D-3D -> pose akhir

Versi "lite" ini pakai:
  - Retrieval : color histogram similarity (pengganti NetVLAD, jauh lebih
                murah tapi konsepnya sama: representasi ringkas per gambar)
  - Matching  : ORB + BFMatcher (pengganti SuperPoint+SuperGlue)
  - Pose      : solvePnPRansac (SAMA seperti versi production)

UPGRADE KE HLOC ASLI (kalau device kuat / server ber-GPU):
  pip install hloc  (dari github.com/cvg/Hierarchical-Localization)
  ganti fungsi extract_global_descriptor() & match_local_features() di
  bawah dengan pemanggilan model NetVLAD/SuperPoint - struktur pipeline
  TETAP SAMA, cuma bagian "otak"-nya di-upgrade.

CARA PAKAI:
  Sebagai server relocalization:
    python3 hloc_lite.py serve --map path/to/map_db.json --port 8000
  Sebagai CLI satu-kali:
    python3 hloc_lite.py localize --map path/to/map_db.json --query foto.jpg
  Bikin map database dari folder foto + pose (hasil COLMAP modul 02):
    python3 hloc_lite.py build-map --images folder/ --poses poses.json --out map_db.json
"""

import argparse
import json
import zlib
from pathlib import Path

import numpy as np
import cv2


def stable_seed(name: str) -> int:
    """hash() bawaan Python di-randomize per proses (security feature) -
    TIDAK deterministik antar proses. Pakai crc32 supaya seed sama persis
    di server maupun di client, walau dijalankan sebagai proses terpisah."""
    return zlib.crc32(name.encode()) % (2**31)


K = np.array([[800, 0, 320], [0, 800, 240], [0, 0, 1]], dtype=np.float32)


def extract_global_descriptor(img_gray: np.ndarray) -> np.ndarray:
    """Pengganti ringan NetVLAD: histogram intensitas + gradien sebagai
    representasi ringkas 1 gambar untuk RETRIEVAL cepat."""
    hist = cv2.calcHist([img_gray], [0], None, [64], [0, 256]).flatten()
    hist = hist / (np.linalg.norm(hist) + 1e-8)
    return hist


def extract_local_features(img_gray: np.ndarray):
    orb = cv2.ORB_create(nfeatures=1500)
    kp, des = orb.detectAndCompute(img_gray, None)
    return kp, des


class MapDatabase:
    """Peta = kumpulan keyframe. Tiap keyframe punya:
       - global descriptor (untuk retrieval)
       - local descriptor + titik 3D yang berasosiasi (untuk PnP)
    Di sistem nyata, titik 3D didapat dari hasil COLMAP (Modul 02)."""

    def __init__(self):
        self.keyframes = []  # list of dict

    def add_keyframe(self, name, img_gray, points_3d_for_keypoints):
        global_desc = extract_global_descriptor(img_gray)
        kp, des = extract_local_features(img_gray)
        self.keyframes.append({
            "name": name,
            "global_desc": global_desc,
            "kp": kp,
            "des": des,
            "points_3d": points_3d_for_keypoints,  # sejajar index dgn kp
        })

    def retrieve_top_k(self, query_global_desc, k=3):
        sims = []
        for i, kf in enumerate(self.keyframes):
            sim = float(np.dot(query_global_desc, kf["global_desc"]))
            sims.append((sim, i))
        sims.sort(reverse=True)
        return [i for _, i in sims[:k]]

    def save(self, path):
        # Simplifikasi: hanya simpan metadata (deskriptor real disimpan
        # sbg npy terpisah di implementasi production)
        meta = [{"name": kf["name"], "n_points": len(kf["points_3d"])}
                for kf in self.keyframes]
        Path(path).write_text(json.dumps(meta, indent=2))


def localize_query(map_db: MapDatabase, query_gray: np.ndarray, top_k=3):
    """Alur inti hloc: retrieve -> match -> PnP"""
    q_global = extract_global_descriptor(query_gray)
    candidate_idxs = map_db.retrieve_top_k(q_global, k=top_k)
    print(f"[RETRIEVAL] Kandidat keyframe paling mirip: "
          f"{[map_db.keyframes[i]['name'] for i in candidate_idxs]}")

    q_kp, q_des = extract_local_features(query_gray)
    if q_des is None:
        raise RuntimeError("Tidak ada fitur terdeteksi di foto query")

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    best_result = None
    for idx in candidate_idxs:
        kf = map_db.keyframes[idx]
        if kf["des"] is None:
            continue
        matches = matcher.match(q_des, kf["des"])
        matches = sorted(matches, key=lambda m: m.distance)[:200]
        if len(matches) < 6:
            continue

        pts_2d = np.float32([q_kp[m.queryIdx].pt for m in matches])
        pts_3d = np.float32([kf["points_3d"][m.trainIdx] for m in matches])

        ok, rvec, tvec, inliers = cv2.solvePnPRansac(
            pts_3d, pts_2d, K, None, reprojectionError=4.0, confidence=0.999
        )
        n_inliers = len(inliers) if ok and inliers is not None else 0

        if ok and (best_result is None or n_inliers > best_result["n_inliers"]):
            best_result = {
                "keyframe": kf["name"],
                "rvec": rvec.flatten().tolist(),
                "tvec": tvec.flatten().tolist(),
                "n_inliers": n_inliers,
            }

    if best_result is None:
        raise RuntimeError("Localization gagal - tidak cukup kecocokan di semua kandidat")

    print(f"[LOCALIZE] Berhasil terhadap keyframe '{best_result['keyframe']}' "
          f"dengan {best_result['n_inliers']} inliers")
    print(f"  posisi (tvec): {best_result['tvec']}")
    print(f"  rotasi (rvec): {best_result['rvec']}")
    return best_result


def render_room_with_known_geometry(rng, room_points_3d, rvec, tvec):
    """Render 'foto' dari titik-titik 3D dengan pose kamera yang DIKETAHUI,
    dan tempel patch tekstur unik di tiap titik supaya ORB bisa mendeteksi
    & mencocokkan (mirip cara demo pertama, tapi sekarang bertekstur supaya
    valid untuk feature matching, bukan cuma titik polos).

    Return: image, dan daftar (posisi_2d, titik_3d_terkait) yang valid.
    """
    img = np.full((480, 640), 45, dtype=np.uint8)
    pts_2d = project(room_points_3d, rvec, tvec)

    visible = []
    for (x, y), pt3d in zip(pts_2d, room_points_3d):
        xi, yi = int(x), int(y)
        if not (10 <= xi < 630 and 10 <= yi < 470):
            continue
        # patch tekstur unik per titik (seeded dari koordinat 3D-nya
        # supaya titik yang sama selalu menghasilkan tekstur sama persis,
        # mensimulasikan "penampakan visual" objek fisik yang konsisten)
        local_rng = np.random.default_rng(int(abs(pt3d.sum() * 1000)) % (2**31))
        patch = local_rng.integers(0, 255, size=(9, 9), dtype=np.uint8)
        y0, y1 = max(0, yi - 4), min(480, yi + 5)
        x0, x1 = max(0, xi - 4), min(640, xi + 5)
        img[y0:y1, x0:x1] = patch[: y1 - y0, : x1 - x0]
        visible.append(((xi, yi), pt3d))

    return img, visible


def project(points_3d, rvec, tvec):
    pts_2d, _ = cv2.projectPoints(points_3d.astype(np.float32), rvec, tvec, K, None)
    return pts_2d.reshape(-1, 2)


def build_demo_map():
    """Bikin map database sintetis TAPI dengan geometri kamera yang valid,
    supaya PnP di localize_query() benar-benar bisa jalan seperti sistem asli."""
    map_db = MapDatabase()
    room_pose = {
        "room_0": (np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 0.0])),
        "room_1": (np.array([0.0, 0.1, 0.0]), np.array([0.0, 0.0, 0.0])),
        "room_2": (np.array([0.0, -0.2, 0.0]), np.array([0.0, 0.0, 0.0])),
    }
    for name, (rvec, tvec) in room_pose.items():
        rng = np.random.default_rng(stable_seed(name))
        points_3d = rng.uniform([-4, -2, 6], [4, 2, 14], size=(250, 3))
        img, visible = render_room_with_known_geometry(rng, points_3d, rvec, tvec)

        kp, des = extract_local_features(img)
        if des is None:
            continue

        # Asosiasikan tiap keypoint hasil deteksi ORB ke titik 3D terdekat
        # (radius kecil) - ini menggantikan proses "triangulation +
        # feature-to-3D association" yang di real system dilakukan COLMAP.
        visible_2d = np.array([v[0] for v in visible])
        visible_3d = np.array([v[1] for v in visible])
        matched_kp, matched_des, matched_3d = [], [], []
        for k, d in zip(kp, des):
            dists = np.linalg.norm(visible_2d - np.array(k.pt), axis=1)
            j = np.argmin(dists)
            if dists[j] < 5.0:
                matched_kp.append(k)
                matched_des.append(d)
                matched_3d.append(visible_3d[j])

        if len(matched_kp) < 8:
            continue

        global_desc = extract_global_descriptor(img)
        map_db.keyframes.append({
            "name": name,
            "global_desc": global_desc,
            "kp": matched_kp,
            "des": np.array(matched_des, dtype=np.uint8),
            "points_3d": np.array(matched_3d, dtype=np.float32),
        })
        print(f"[MAP BUILD] {name}: {len(matched_kp)} fitur beranotasi titik 3D")

    return map_db


def cli_demo():
    print("=== DEMO end-to-end: retrieval + matching + PnP (data sintetis, geometri valid) ===\n")
    map_db = build_demo_map()

    # Query: kamera BARU melihat ruangan yang sama dengan room_1 tapi dari
    # posisi sedikit bergeser (simulasi device baru datang ke lokasi ini)
    rng = np.random.default_rng(stable_seed("room_1"))
    points_3d = rng.uniform([-4, -2, 6], [4, 2, 14], size=(250, 3))
    true_rvec_query = np.array([0.02, 0.12, 0.0])
    true_tvec_query = np.array([0.3, 0.05, -0.5])
    query_img, _ = render_room_with_known_geometry(rng, points_3d, true_rvec_query, true_tvec_query)

    print(f"[QUERY] Posisi asli device (ground truth, dirahasiakan dari sistem): "
          f"{true_tvec_query}\n")

    result = localize_query(map_db, query_img)
    err = np.linalg.norm(np.array(result["tvec"]) - true_tvec_query)
    print(f"\n[EVALUASI] Error posisi vs ground truth: {err:.4f} unit")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("demo")
    args = ap.parse_args()

    if args.cmd == "demo" or args.cmd is None:
        cli_demo()
