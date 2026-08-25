//
// OpenVPS - Module 04: ARKit Client
// ====================================
// Jalan di iPhone/iPad. Tugasnya dua:
//   1. Pakai ARKit VIO on-device untuk tracking real-time yang halus
//      (60fps, sudah built-in di iOS, akurat jangka pendek)
//   2. Setiap beberapa detik, kirim 1 frame kamera ke server OpenVPS
//      (Modul 04 server.py) untuk KOREKSI drift posisi
//
// Install: buat project iOS baru di Xcode, ARKit framework sudah built-in,
// tempel kode ini ke ViewController.

import ARKit
import UIKit

class ViewController: UIViewController, ARSessionDelegate {

    let arView = ARSCNView()
    let session = ARSession()

    // Ganti dengan alamat VPS kamu setelah deploy (lihat docs/deploy_to_vps.md)
    let relocalizationServerURL = URL(string: "https://vps-kamu.example.com/relocalize")!

    var lastRelocalizationTime: TimeInterval = 0
    let relocalizationInterval: TimeInterval = 5.0  // kirim foto tiap 5 detik

    override func viewDidLoad() {
        super.viewDidLoad()
        arView.session = session
        session.delegate = self
        view.addSubview(arView)

        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal, .vertical]
        session.run(config)
    }

    // Dipanggil ARKit tiap frame (~60fps) - ini VIO on-device, sudah akurat
    // untuk jangka pendek tanpa perlu server sama sekali
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let vioPose = frame.camera.transform  // 4x4 matrix posisi+rotasi VIO

        // Cek apakah sudah waktunya minta koreksi dari server
        let now = frame.timestamp
        if now - lastRelocalizationTime > relocalizationInterval {
            lastRelocalizationTime = now
            sendFrameForRelocalization(frame: frame, currentVIOPose: vioPose)
        }

        // Di production: gabungkan vioPose (halus, real-time) dengan hasil
        // koreksi server (akurat, tapi datang telat ~beberapa ratus ms)
        // pakai teknik sensor fusion sederhana (mis. exponential smoothing
        // atau Kalman filter) supaya transisi tidak "loncat".
    }

    func sendFrameForRelocalization(frame: ARFrame, currentVIOPose: simd_float4x4) {
        // Ambil 1 frame kamera, kompres jadi JPEG, kirim ke server VPS
        let pixelBuffer = frame.capturedImage
        guard let jpegData = jpegData(from: pixelBuffer) else { return }

        var request = URLRequest(url: relocalizationServerURL)
        request.httpMethod = "POST"
        let boundary = UUID().uuidString
        request.setValue("multipart/form-data; boundary=\(boundary)",
                          forHTTPHeaderField: "Content-Type")

        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"image\"; filename=\"frame.jpg\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(jpegData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)

        URLSession.shared.uploadTask(with: request, from: body) { data, response, error in
            guard let data = data, error == nil else { return }
            if let result = try? JSONDecoder().decode(RelocalizationResult.self, from: data),
               result.success {
                DispatchQueue.main.async {
                    self.applyServerCorrection(result: result, vioPoseAtCaptureTime: currentVIOPose)
                }
            }
        }.resume()
    }

    func applyServerCorrection(result: RelocalizationResult, vioPoseAtCaptureTime: simd_float4x4) {
        // Di sini posisi VIO yang sudah drift dikoreksi ke posisi absolut
        // hasil relocalization server. Implementasi penuh butuh transform
        // antara "koordinat map server" <-> "koordinat ARKit session" -
        // biasanya via ARKit World Origin realignment atau anchor injection.
        print("Server correction diterima: keyframe=\(result.keyframe ?? "-"), " +
              "posisi=\(result.position ?? []), inliers=\(result.n_inliers ?? 0)")
    }

    func jpegData(from pixelBuffer: CVPixelBuffer) -> Data? {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let context = CIContext()
        guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else { return nil }
        let uiImage = UIImage(cgImage: cgImage)
        return uiImage.jpegData(compressionQuality: 0.8)
    }
}

struct RelocalizationResult: Codable {
    let success: Bool
    let keyframe: String?
    let position: [Float]?
    let rotation: [Float]?
    let n_inliers: Int?
    let message: String?
}
