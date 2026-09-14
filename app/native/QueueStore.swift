import Foundation

struct QueuedLink: Codable {
  let url: String
  let timestamp: Double
}

final class QueueStore {
  let directory: URL
  let container: URL
  init(root: URL? = nil) throws {
    container = try root ?? ShareDiagnostics.container()
    directory = container.appendingPathComponent("pending-links", isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
  }
  @discardableResult func enqueue(url: String, timestamp: Double) throws -> String {
    guard url.utf8.count <= 8192, timestamp.isFinite else { throw NSError(domain: "ReelBotQueue", code: 3, userInfo: [NSLocalizedDescriptionKey: "Invalid or oversized shared link."]) }
    let id = UUID().uuidString
    let data = try JSONEncoder().encode(QueuedLink(url: url, timestamp: timestamp))
    let destination = directory.appendingPathComponent(id + ".json")
    ShareDiagnostics.event("write_attempt", "id=\(id)")
    do { try data.write(to: destination, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]) }
    catch { ShareDiagnostics.event("write_failed", error.localizedDescription); throw error }
    ShareDiagnostics.event("write_succeeded", "id=\(id)")
    return id
  }
  func read() throws -> [[String: Any]] {
    var entries: [[String: Any]] = [], failures: [String] = []
    var quarantined = 0
    let files = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil).filter { $0.pathExtension == "json" }
    for file in files {
      do {
        let id = file.deletingPathExtension().lastPathComponent
        guard UUID(uuidString: id) != nil else { throw NSError(domain: "ReelBotQueue", code: 4, userInfo: [NSLocalizedDescriptionKey: "Invalid queue filename."]) }
        let data = try Data(contentsOf: file, options: .mappedIfSafe)
        guard data.count <= 16384 else { throw NSError(domain: "ReelBotQueue", code: 5, userInfo: [NSLocalizedDescriptionKey: "Oversized queue item."]) }
        let value = try JSONDecoder().decode(QueuedLink.self, from: data)
        guard value.timestamp.isFinite, value.timestamp >= 0, value.timestamp < 8640000000000000, !value.url.isEmpty, value.url.utf8.count <= 8192 else { throw NSError(domain: "ReelBotQueue", code: 6, userInfo: [NSLocalizedDescriptionKey: "Invalid queue payload."]) }
        entries.append(["id": id, "url": value.url, "timestamp": value.timestamp])
      } catch {
        failures.append("\(file.lastPathComponent): \(error.localizedDescription)")
        do { try quarantineFile(file, reason: error.localizedDescription); quarantined += 1 }
        catch { ShareDiagnostics.event("quarantine_failed", error.localizedDescription) }
      }
    }
    ShareDiagnostics.event("queue_read", "count=\(files.count) readable=\(entries.count) failures=\(failures.count)")
    try ShareDiagnostics.write(["timestamp": Date().timeIntervalSince1970 * 1000, "count": files.count, "failures": failures, "quarantined": quarantined], to: container.appendingPathComponent("last-queue-read.json"))
    return entries.sorted { ($0["timestamp"] as? Double ?? 0) < ($1["timestamp"] as? Double ?? 0) }
  }
  private func quarantineFile(_ file: URL, reason: String) throws {
    let quarantine = container.appendingPathComponent("quarantined-links", isDirectory: true)
    try FileManager.default.createDirectory(at: quarantine, withIntermediateDirectories: true)
    let destination = quarantine.appendingPathComponent(UUID().uuidString + "-" + file.lastPathComponent)
    if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.moveItem(at: file, to: destination) }
    try ShareDiagnostics.write(["timestamp": Date().timeIntervalSince1970 * 1000, "error": reason, "file": file.lastPathComponent], to: destination.appendingPathExtension("reason"))
    ShareDiagnostics.event("item_quarantined", "file=\(file.lastPathComponent) error=\(reason)")
  }
  func quarantine(id: String, reason: String) throws {
    guard UUID(uuidString: id) != nil else { throw NSError(domain: "ReelBotQueue", code: 2) }
    try quarantineFile(directory.appendingPathComponent(id + ".json"), reason: reason)
  }
  func acknowledge(id: String) throws {
    guard UUID(uuidString: id) != nil else { throw NSError(domain: "ReelBotQueue", code: 2) }
    let file = directory.appendingPathComponent(id + ".json")
    if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
  }
}
