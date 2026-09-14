import Foundation
import React
import MapKit
import UIKit
import CryptoKit
#if MOTION_PROFILE
import UIKit
#endif

@objc(ReelBotQueue)
final class ReelBotQueue: NSObject {
  private var snapshots: [String: MKMapSnapshotter] = [:]
  @objc(mapThumbnail:latitude:longitude:color:resolver:rejecter:)
  func mapThumbnail(_ place: String, latitude: Double, longitude: Double, color: String, resolver: @escaping RCTPromiseResolveBlock, rejecter: @escaping RCTPromiseRejectBlock) {
    guard UUID(uuidString: place) != nil, latitude.isFinite, longitude.isFinite, abs(latitude)<=90, abs(longitude)<=180 else { rejecter("map_coordinates", "Map is unavailable.", nil); return }
    let key = SHA256.hash(data: Data("v2:360x270:1x:\(place):\(latitude):\(longitude):\(color)".utf8)).map { String(format: "%02x", $0) }.joined()
    DispatchQueue.main.async {
      do {
        let directory = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("venue-maps", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let target = directory.appendingPathComponent(key + ".jpg")
        if FileManager.default.fileExists(atPath: target.path) { resolver(target.absoluteString); return }
        let options = MKMapSnapshotter.Options()
        let coordinate = CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
        options.region = MKCoordinateRegion(center: coordinate, latitudinalMeters: 900, longitudinalMeters: 1200)
        options.size = CGSize(width: 360, height: 270); options.scale = 1
        options.mapType = .standard; options.showsBuildings = true; options.pointOfInterestFilter = .excludingAll
        options.traitCollection = UITraitCollection(userInterfaceStyle: .light)
        let snapshotter = MKMapSnapshotter(options: options); self.snapshots[key] = snapshotter
        snapshotter.start { snapshot, error in
          self.snapshots.removeValue(forKey: key)
          guard let snapshot = snapshot else { rejecter("map_snapshot", "Map preview unavailable.", error); return }
          let format = UIGraphicsImageRendererFormat()
          format.scale = 1; format.opaque = true
          let renderer = UIGraphicsImageRenderer(size: options.size, format: format)
          let image = renderer.image { context in
            snapshot.image.draw(at: .zero)
            let point = snapshot.point(for: coordinate)
            var hex: UInt64 = 0; Scanner(string: color.replacingOccurrences(of: "#", with: "")).scanHexInt64(&hex)
            let fill = UIColor(red: CGFloat((hex>>16)&255)/255, green: CGFloat((hex>>8)&255)/255, blue: CGFloat(hex&255)/255, alpha: 1)
            context.cgContext.setFillColor(UIColor.white.cgColor);context.cgContext.fillEllipse(in: CGRect(x:point.x-12,y:point.y-12,width:24,height:24))
            context.cgContext.setFillColor(fill.cgColor);context.cgContext.fillEllipse(in: CGRect(x:point.x-9,y:point.y-9,width:18,height:18))
          }
          do {
            guard let data=image.jpegData(compressionQuality: 0.8) else { throw NSError(domain:"ReelBot",code:1) }
            try data.write(to: target, options: .atomic)
            resolver(target.absoluteString)
          } catch { rejecter("map_cache", "Map preview unavailable.", error) }
        }
        DispatchQueue.main.asyncAfter(deadline: .now()+10) { if self.snapshots[key] === snapshotter { snapshotter.cancel() } }
      } catch { rejecter("map_cache", "Map preview unavailable.", error) }
    }
  }
  @objc static func requiresMainQueueSetup() -> Bool { false }
  @objc func constantsToExport() -> [AnyHashable: Any] {
#if MOTION_PROFILE
    return ["debugFeaturesEnabled": false, "motionProfile": true, "visualFixture": ProcessInfo.processInfo.arguments.contains("--reelbot-visual-fixture"), "fixtureCount": ProcessInfo.processInfo.environment["REELBOT_FIXTURE_COUNT"] ?? "240"]
#elseif DEBUG
    return ["debugFeaturesEnabled": true, "visualFixture": ProcessInfo.processInfo.arguments.contains("--reelbot-visual-fixture"), "fixtureGrayscale": ProcessInfo.processInfo.environment["REELBOT_FIXTURE_GRAYSCALE"] == "1", "fixtureCount": ProcessInfo.processInfo.environment["REELBOT_FIXTURE_COUNT"] ?? "50"]
#else
    return ["debugFeaturesEnabled": false]
#endif
  }
  @objc(beginMotionProfile:duration:)
  func beginMotionProfile(_ label: String, duration: Double) {
#if MOTION_PROFILE
    DispatchQueue.main.async { NativeMotionSampler.shared.begin(label, duration: duration) }
#endif
  }
  @objc(enqueue:timestamp:resolver:rejecter:)
  func enqueue(_ url: String, timestamp: Double, resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    do { resolver(try QueueStore().enqueue(url: url, timestamp: timestamp)) }
    catch { rejecter("queue_write", "Could not save this link on the device.", error) }
  }
  @objc(read:rejecter:)
  func read(_ resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    do { resolver(try QueueStore().read()) }
    catch { rejecter("queue_read", "Could not read pending links. Please retry.", error) }
  }
  @objc(acknowledge:resolver:rejecter:)
  func acknowledge(_ id: String, resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    do { try QueueStore().acknowledge(id: id); resolver(nil) }
    catch { rejecter("queue_ack", "Could not finish reading a saved link.", error) }
  }
  @objc(quarantine:reason:resolver:rejecter:)
  func quarantine(_ id: String, reason: String, resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    do { try QueueStore().quarantine(id: id, reason: reason); resolver(nil) }
    catch { rejecter("queue_quarantine", error.localizedDescription, error) }
  }
  @objc(recordDrain:resolver:rejecter:)
  func recordDrain(_ report: NSDictionary, resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    var value = report as? [String: Any] ?? [:]
    do {
      let root = try ShareDiagnostics.container(), scanFile = root.appendingPathComponent("last-queue-read.json")
      if let data = try? Data(contentsOf: scanFile), let scan = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any], (scan["timestamp"] as? Double ?? 0) >= (value["timestamp"] as? Double ?? .infinity) {
        let errors = scan["failures"] as? [String] ?? []
        value["count"] = max(value["count"] as? Int ?? 0, scan["count"] as? Int ?? 0)
        value["failures"] = (value["failures"] as? Int ?? 0) + errors.count
        value["quarantined"] = (value["quarantined"] as? Int ?? 0) + (scan["quarantined"] as? Int ?? 0)
        value["errors"] = (value["errors"] as? [String] ?? []) + errors
      }
      ShareDiagnostics.event("drain", "\(value)")
      try ShareDiagnostics.write(value, to: root.appendingPathComponent("last-drain.json"))
      if (value["count"] as? Int ?? 0) > 0 || (value["failures"] as? Int ?? 0) > 0 {
        try ShareDiagnostics.write(value, to: root.appendingPathComponent("last-meaningful-drain.json"))
      }
      resolver(value)
    } catch { ShareDiagnostics.event("drain_trace_failed", "\(value) error=\(error.localizedDescription)"); rejecter("drain_trace", error.localizedDescription, error) }
  }
  @objc(canary:rejecter:)
  func canary(_ resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    do { resolver(try ShareDiagnostics.canary()) }
    catch { rejecter("canary", error.localizedDescription, error) }
  }

  @objc(diagnostics:rejecter:)
  func diagnostics(_ resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    var result: [String: Any] = ["container_id": ShareDiagnostics.containerID, "reachable": false]
#if DEBUG
    result["configuration"] = "Debug"
#else
    result["configuration"] = "Release"
#endif
    result["version"] = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") ?? "unknown"
    result["build"] = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") ?? "unknown"
    do {
      let root = try ShareDiagnostics.container(); result["path"] = root.path
      _ = try FileManager.default.contentsOfDirectory(atPath: root.path)
      result["reachable"] = true
      for (key, name) in [("attempts", "share-attempts.json"), ("last_drain", "last-drain.json"), ("last_meaningful_drain", "last-meaningful-drain.json"), ("last_queue_read", "last-queue-read.json")] {
        let file = root.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: file.path) {
          do {
            let data = try Data(contentsOf: file, options: .mappedIfSafe)
            guard data.count <= 1048576 else { throw NSError(domain: "ReelBotQueue", code: 8, userInfo: [NSLocalizedDescriptionKey: "Diagnostic file exceeds 1 MB."]) }
            let value = try JSONSerialization.jsonObject(with: data)
            if key == "attempts", !(value is [[String: Any]]) { throw NSError(domain: "ReelBotQueue", code: 8, userInfo: [NSLocalizedDescriptionKey: "Attempt log is not a JSON array."]) }
            result[key] = value
          }
          catch { result[key + "_error"] = error.localizedDescription }
        }
      }
      // Inspection never drains, creates the queue, or repairs evidence.
      for (key, folder) in [("pending", "pending-links"), ("quarantine", "quarantined-links")] {
        let directory = root.appendingPathComponent(folder)
        var rows: [[String: Any]] = []
        if FileManager.default.fileExists(atPath: directory.path) {
          do {
            for file in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.contentModificationDateKey]).sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
              var row: [String: Any] = ["file": file.lastPathComponent]
              do {
                let data = try Data(contentsOf: file, options: .mappedIfSafe)
                row["raw"] = String(data: data.prefix(16384), encoding: .utf8) ?? "[Non-UTF8 data]"
                row["truncated"] = data.count > 16384
                row["modified_at"] = try file.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate?.timeIntervalSince1970 as Any? ?? NSNull()
              } catch { row["error"] = error.localizedDescription }
              rows.append(row)
            }
          } catch { result[key + "_error"] = error.localizedDescription }
        }
        result[key] = rows
      }
    } catch { result["error"] = error.localizedDescription }
    resolver(result)
  }
}

#if MOTION_PROFILE
// CADisplayLink avoids duplicate/non-monotonic Reanimated callbacks during native scrolling.
// This measures main-thread display-link pacing, not GPU render/presentation duration.
private final class NativeMotionSampler: NSObject {
  static let shared = NativeMotionSampler()
  private var link: CADisplayLink?
  private var label = ""
  private var duration = 0.0
  private var first = 0.0
  private var previous = 0.0
  private var intervals = [Double]()
  private var reports = [[String: Any]]()
  private let writer = DispatchQueue(label: "reelbot.motion-profile.write")
  private let path = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    .appendingPathComponent("motion-native-profile-\(Int(Date().timeIntervalSince1970 * 1000)).json")
  func begin(_ name: String, duration: Double) {
    if link != nil && ((label == "card-detail" && name == "sheet-snap") || (label == "map-peek-drag" && name == "map-peek-snap")) { return }
    link?.invalidate()
    label = name; self.duration = duration; first = 0; previous = 0; intervals = []
    let displayLink = CADisplayLink(target: self, selector: #selector(tick(_:)))
    displayLink.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 120, preferred: 120)
    displayLink.add(to: .main, forMode: .common)
    link = displayLink
  }
  @objc private func tick(_ displayLink: CADisplayLink) {
    let now = displayLink.timestamp
    guard previous > 0 else { first = now; previous = now; return }
    guard now > previous else { return }
    intervals.append((now - previous) * 1000); previous = now
    guard (now - first) * 1000 >= duration else { return }
    displayLink.invalidate(); link = nil
    let sorted = intervals.sorted(), elapsed = intervals.reduce(0, +)
    var report: [String: Any] = [:]
    report["label"] = label
    report["reduced_motion"] = UIAccessibility.isReduceMotionEnabled
    report["timestamp"] = ISO8601DateFormatter().string(from: Date())
    report["fixtureEntries"] = Int(ProcessInfo.processInfo.environment["REELBOT_FIXTURE_COUNT"] ?? "240") ?? 240
    report["metric"] = "CADisplayLink timestamp intervals; main-thread pacing, not GPU render duration"
    report["method_version"] = 2
    report["samples"] = intervals.count
    report["elapsed_ms"] = elapsed
    report["average_fps"] = Double(intervals.count) * 1000 / elapsed
    report["p95_ms"] = sorted[min(sorted.count - 1, Int(Double(sorted.count) * 0.95))]
    report["max_ms"] = sorted.last ?? 0
    report["over_16_7_ms"] = intervals.filter { $0 > 16.7 }.count
    report["over_8_33_ms"] = intervals.filter { $0 > 8.34 }.count
    report["intervals_ms"] = intervals
    reports.append(report)
    let snapshot = reports, destination = path
    writer.async {
      if let data = try? JSONSerialization.data(withJSONObject: snapshot, options: [.prettyPrinted, .sortedKeys]) {
        try? data.write(to: destination, options: .atomic)
      }
    }
  }
}
#endif
