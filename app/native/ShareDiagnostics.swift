import Foundation
import Darwin
import os.log

enum ShareDiagnostics {
  static var containerID: String { Bundle.main.object(forInfoDictionaryKey: "AppGroupIdentifier") as? String ?? "group.com.krishwaghani.reelbot" }
  static let log = OSLog(subsystem: "com.krishwaghani.reelbot.share", category: "diagnostics")
  static func event(_ name: String, _ detail: String = "") {
    os_log("container_id=%{public}@ event=%{public}@ detail=%{public}@", log: log, type: .info, containerID, name, detail)
  }
  static func memoryBytes() -> UInt64? {
    var info = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
    let result = withUnsafeMutablePointer(to: &info) { pointer in
      pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
        task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
      }
    }
    return result == KERN_SUCCESS ? info.phys_footprint : nil
  }
  static func container() throws -> URL {
    guard let url = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: containerID) else {
      event("container_unavailable")
      throw NSError(domain: "ReelBotQueue", code: 1, userInfo: [NSLocalizedDescriptionKey: "App Group container unavailable: \(containerID)"])
    }
    event("container_resolved", url.path)
    return url
  }
  static func write(_ object: Any, to file: URL) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    try data.write(to: file, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
  }
  static func canary(root: URL? = nil) throws -> [String: Any] {
    let directory = try root ?? container(), value = "\(Date().timeIntervalSince1970):\(UUID().uuidString)"
    let file = directory.appendingPathComponent("canary-\(UUID().uuidString).json")
    defer { try? FileManager.default.removeItem(at: file) }
    try write(["value": value], to: file)
    let read = try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: String]
    guard read?["value"] == value else { throw NSError(domain: "ReelBotQueue", code: 7, userInfo: [NSLocalizedDescriptionKey: "Canary read-back does not match the written value."]) }
    return ["success": true, "timestamp": Date().timeIntervalSince1970 * 1000, "path": directory.path]
  }
  // A process-shared lock prevents concurrent extension launches losing log entries.
  static func locked<T>(root: URL, _ action: () throws -> T) throws -> T {
    let fd = open(root.appendingPathComponent("share-diagnostics.lock").path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
    guard fd >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    defer { close(fd) }
    // Never hang the extension waiting on another process.
    let deadline = ProcessInfo.processInfo.systemUptime + 0.1
    while flock(fd, LOCK_EX | LOCK_NB) != 0 {
      guard errno == EWOULDBLOCK, ProcessInfo.processInfo.systemUptime < deadline else {
        throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno))
      }
      usleep(1000)
    }
    defer { flock(fd, LOCK_UN) }
    return try action()
  }
  static func append(_ attempt: [String: Any], root: URL? = nil) throws {
    let directory = try root ?? container()
    try locked(root: directory) {
      let file = directory.appendingPathComponent("share-attempts.json")
      var entries: [[String: Any]] = []
      if FileManager.default.fileExists(atPath: file.path) {
        do {
          let data = try Data(contentsOf: file, options: .mappedIfSafe)
          guard data.count <= 1048576, let decoded = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            throw NSError(domain: "ReelBotQueue", code: 8, userInfo: [NSLocalizedDescriptionKey: "Invalid or oversized attempt log."])
          }
          entries = decoded
        }
        catch {
          // Preserve damaged diagnostics separately; don't prevent future evidence.
          let corrupt = directory.appendingPathComponent("share-attempts.corrupt.json")
          if FileManager.default.fileExists(atPath: corrupt.path) { try FileManager.default.removeItem(at: corrupt) }
          try FileManager.default.moveItem(at: file, to: corrupt)
          event("attempt_log_quarantined", error.localizedDescription)
        }
      }
      if let id = attempt["attempt_id"] as? String { entries.removeAll { $0["attempt_id"] as? String == id } }
      entries.append(attempt)
      try write(Array(entries.suffix(50)), to: file)
    }
  }
}
