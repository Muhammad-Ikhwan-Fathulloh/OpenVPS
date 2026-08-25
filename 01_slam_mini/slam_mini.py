"""
OpenVPS - Module 01: SLAM-Mini
================================
Konsep dasar yang sama dengan ORB-SLAM3 / RTAB-Map, versi ringan Python:

  frame(t)   -> deteksi fitur ORB
  frame(t+1) -> deteksi fitur ORB
             -> cocokkan fitur antar frame (feature matching)
             -> hitung Essential Matrix (gerakan kamera antar 2 frame)
             -> recoverPose -> dapat rotasi (R) & arah translasi (t)
             -> akumulasi -> lintasan (trajectory) kamera dari waktu ke waktu

Ini disebut Visual Odometry (VO) - inti dari SLAM. SLAM penuh menambahkan:
  - Loop closure (deteksi saat kembali ke tempat yang sama -> koreksi drift)
  - Bundle adjustment (optimasi global semua pose + titik 3D sekaligus)
  - Map management (keyframe selection, local/global map)

Untuk belajar ORB-SLAM3 / RTAB-Map SUNGGUHAN (C++, lebih akurat & scalable):
  lihat docs/upgrade_to_orbslam3.md di folder ini.

CARA PAKAI:
  python3 slam_mini.py --video path/to/video.mp4
  python3 slam_mini.py --webcam        # pakai webcam
  python3 slam_mini.py --demo          # generate video sintetis untuk tes cepat
"""

import argparse
import numpy as np
import cv2


class VisualOdometry:
    def __init__(self, K: np.ndarray, n_features: int = 2000):
        self.K = K
        self.orb = cv2.ORB_create(nfeatures=n_features)
        self.matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        self.prev_kp = None
        self.prev_des = None
        self.prev_frame = None

        # Trajectory: mulai dari origin, identity rotation
        self.cur_R = np.eye(3)
        self.cur_t = np.zeros((3, 1))
        self.trajectory = [self.cur_t.flatten().copy()]

    def process_frame(self, frame_gray: np.ndarray):
        kp, des = self.orb.detectAndCompute(frame_gray, None)

        if self.prev_des is None or des is None or len(kp) < 8:
            self.prev_kp, self.prev_des, self.prev_frame = kp, des, frame_gray
            return None, 0

        matches = self.matcher.match(self.prev_des, des)
        matches = sorted(matches, key=lambda m: m.distance)[:300]  # ambil terbaik

        if len(matches) < 8:
            self.prev_kp, self.prev_des, self.prev_frame = kp, des, frame_gray
            return None, len(matches)

        pts_prev = np.float32([self.prev_kp[m.queryIdx].pt for m in matches])
        pts_cur = np.float32([kp[m.trainIdx].pt for m in matches])

        E, mask = cv2.findEssentialMat(
            pts_cur, pts_prev, self.K, method=cv2.RANSAC, prob=0.999, threshold=1.0
        )
        if E is None:
            self.prev_kp, self.prev_des, self.prev_frame = kp, des, frame_gray
            return None, len(matches)

        _, R, t, mask_pose = cv2.recoverPose(E, pts_cur, pts_prev, self.K)

        # Akumulasi pose (catatan: skala translasi monocular tidak diketahui
        # secara absolut tanpa info tambahan seperti IMU/stereo - ini
        # keterbatasan klasik monocular VO, makanya VIO menambah IMU)
        self.cur_t = self.cur_t + self.cur_R @ t
        self.cur_R = R @ self.cur_R
        self.trajectory.append(self.cur_t.flatten().copy())

        self.prev_kp, self.prev_des, self.prev_frame = kp, des, frame_gray
        # mask_pose berisi 0/255 (uint8), bagi 255 supaya jadi jumlah titik
        n_inliers = int(mask_pose.sum() // 255) if mask_pose is not None else 0
        return (self.cur_R.copy(), self.cur_t.copy()), n_inliers


def make_synthetic_video(path: str, n_frames: int = 60, size=(640, 480)):
    """Bikin video sintetis: kamera bergerak menghadap pola titik random,
    supaya bisa langsung tes VO tanpa perlu file video asli."""
    w, h = size
    rng = np.random.default_rng(0)
    pts = rng.integers(0, [w, h], size=(150, 2))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, 20.0, size)
    for i in range(n_frames):
        frame = np.full((h, w, 3), 30, dtype=np.uint8)
        shift = int(3 * i)  # simulasi kamera bergeser ke kanan
        for (x, y) in pts:
            xi = int(x - shift) % w
            cv2.circle(frame, (xi, y), 4, (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def run(video_source, K):
    cap = cv2.VideoCapture(video_source)
    vo = VisualOdometry(K)
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        pose, n_inliers = vo.process_frame(gray)
        if pose is not None:
            R, t = pose
            print(f"[frame {frame_idx:04d}] inliers={n_inliers:4d}  "
                  f"posisi_kumulatif={t.flatten()}")
        frame_idx += 1
    cap.release()
    print(f"\nTotal frame diproses: {frame_idx}")
    print(f"Panjang trajectory tercatat: {len(vo.trajectory)} titik pose")
    return vo.trajectory


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=str, default=None)
    ap.add_argument("--webcam", action="store_true")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    K = np.array([[800, 0, 320], [0, 800, 240], [0, 0, 1]], dtype=np.float32)

    if args.demo:
        demo_path = "/tmp/synthetic_vo.mp4"
        print("[DEMO] Membuat video sintetis untuk uji cepat...")
        make_synthetic_video(demo_path)
        run(demo_path, K)
    elif args.webcam:
        run(0, K)
    elif args.video:
        run(args.video, K)
    else:
        print("Gunakan --demo, --webcam, atau --video <path>")
