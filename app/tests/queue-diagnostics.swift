import Foundation

@main struct QueueDiagnosticsTests {
  static func main() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("reelbot-diagnostics-" + UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let queue = try QueueStore(root: root)
    let first = try queue.enqueue(url: "https://vm.tiktok.com/Ab123/", timestamp: 1)
    try Data("{\"url\":".utf8).write(to: queue.directory.appendingPathComponent(UUID().uuidString + ".json"))
    try Data("{}".utf8).write(to: queue.directory.appendingPathComponent("not-a-uuid.json"))
    try Data(repeating: 65, count: 20000).write(to: queue.directory.appendingPathComponent(UUID().uuidString + ".json"))
    let second = try queue.enqueue(url: "https://youtu.be/abcdefghijk", timestamp: 2)
    let rows = try queue.read()
    precondition(rows.count == 2 && rows[0]["id"] as? String == first && rows[1]["id"] as? String == second)
    let quarantined = try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("quarantined-links").path)
    precondition(quarantined.filter { $0.hasSuffix(".json") }.count == 3)
    precondition(quarantined.filter { $0.hasSuffix(".reason") }.count == 3)
    let reread = try queue.read(); precondition(reread.count == 2)
    try queue.acknowledge(id: first); try queue.acknowledge(id: first)
    let afterAck = try queue.read(); precondition(afterAck.count == 1)
    let canary = try ShareDiagnostics.canary(root: root); precondition(canary["success"] as? Bool == true)
    do { _ = try ShareDiagnostics.canary(root: root.appendingPathComponent("missing")); preconditionFailure("Missing container must fail") } catch {}
    for i in 0..<55 { try ShareDiagnostics.append(["attempt_id": "\(i)", "outcome": "started"], root: root) }
    try ShareDiagnostics.append(["attempt_id": "54", "outcome": "saved"], root: root)
    let log = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("share-attempts.json"))) as! [[String: Any]]
    precondition(log.count == 50 && log.first?["attempt_id"] as? String == "5" && log.last?["outcome"] as? String == "saved")
    let lock = NSLock(); var failures: [String] = []
    DispatchQueue.concurrentPerform(iterations: 4) { worker in
      for i in 0..<5 {
        do { try ShareDiagnostics.append(["attempt_id": "concurrent-\(worker)-\(i)", "outcome": "saved"], root: root) }
        catch { lock.lock(); failures.append(error.localizedDescription); lock.unlock() }
      }
    }
    precondition(failures.isEmpty)
    let concurrent = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("share-attempts.json"))) as! [[String: Any]]
    precondition(concurrent.filter { ($0["attempt_id"] as? String)?.hasPrefix("concurrent-") == true }.count == 20)
    for url in ["https://vm.tiktok.com/Ab123/", "https://instagram.com/reel/ABC/", "https://youtu.be/abcdefghijk"] { precondition(SharedURL.extract("Watch this: " + url) != nil) }
    for url in ["file:///photo.png", "https://instagram.com.evil.test/reel/ABC", "https://instagram.com/profile/"] { precondition(SharedURL.extract(url) == nil) }
    precondition(SharedURL.extract(String(repeating: "x", count: 20000)) == nil)
    print("PASS: corrupt/partial/oversized files quarantined; later saves readable; idempotent ack; canary success/error; rolling 50 attempts; concurrent writers; supported URLs/text and rejection. macOS native unit tests only.")
  }
}
