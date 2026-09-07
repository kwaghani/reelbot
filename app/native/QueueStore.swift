import Foundation

struct QueuedLink: Codable {
  let url: String
  let timestamp: Double
}

final class QueueStore {
  let directory: URL
  init(root: URL? = nil) throws {
    let identifier = Bundle.main.object(forInfoDictionaryKey: "AppGroupIdentifier") as? String ?? "group.com.krishwaghani.reelbot"
    guard let container = root ?? FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: identifier) else {
      throw NSError(domain: "ReelBotQueue", code: 1, userInfo: [NSLocalizedDescriptionKey: "The save container is unavailable."])
    }
    directory = container.appendingPathComponent("pending-links", isDirectory: true)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
  }
  @discardableResult func enqueue(url: String, timestamp: Double) throws -> String {
    let id = UUID().uuidString
    let data = try JSONEncoder().encode(QueuedLink(url: url, timestamp: timestamp))
    let destination = directory.appendingPathComponent(id + ".json")
    try data.write(to: destination, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    return id
  }
  func read() throws -> [[String: Any]] {
    try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
      .filter { $0.pathExtension == "json" }
      .map { file in
        let value = try JSONDecoder().decode(QueuedLink.self, from: Data(contentsOf: file))
        return ["id": file.deletingPathExtension().lastPathComponent, "url": value.url, "timestamp": value.timestamp] as [String: Any]
      }.sorted { ($0["timestamp"] as? Double ?? 0) < ($1["timestamp"] as? Double ?? 0) }
  }
  func acknowledge(id: String) throws {
    guard UUID(uuidString: id) != nil else { throw NSError(domain: "ReelBotQueue", code: 2) }
    let file = directory.appendingPathComponent(id + ".json")
    if FileManager.default.fileExists(atPath: file.path) { try FileManager.default.removeItem(at: file) }
  }
}
