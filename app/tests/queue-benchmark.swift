import Foundation
import Darwin

@main struct QueueBenchmark {
  static func main() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent("reelbot-queue-test-" + UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let queue = try QueueStore(root: root)
    let links = ["https://www.instagram.com/reel/ABC/", "https://www.tiktok.com/@test/video/123", "https://www.youtube.com/watch?v=abcdefghijk"]
    var milliseconds: [Double] = []
    for number in 0..<50 {
      let start = ProcessInfo.processInfo.systemUptime
      try queue.enqueue(url: links[number % 3], timestamp: Double(number))
      milliseconds.append((ProcessInfo.processInfo.systemUptime - start) * 1000)
    }
    let entries = try QueueStore(root: root).read()
    precondition(entries.count == 50)
    for entry in entries { try queue.acknowledge(id: entry["id"] as! String) }
    let remaining = try queue.read()
    precondition(remaining.isEmpty)
    var info = mach_task_basic_info()
    var count = mach_msg_type_number_t(MemoryLayout<mach_task_basic_info>.size) / 4
    let status = withUnsafeMutablePointer(to: &info) { pointer in
      pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), $0, &count) }
    }
    let result: [String: Any] = ["writes": 50, "lost": 0, "max_write_ms": milliseconds.max()!,
      "mean_write_ms": milliseconds.reduce(0,+)/50, "resident_bytes": status == KERN_SUCCESS ? info.resident_size : 0,
      "scope": "Native QueueStore on macOS; excludes host share sheets, extension presentation and iOS process memory."]
    print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
  }
}
