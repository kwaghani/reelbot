import UIKit
import UniformTypeIdentifiers

final class ShareExtensionViewController: UIViewController {
  private let started = ProcessInfo.processInfo.systemUptime
  private let attemptID = UUID().uuidString
  private var finished = false
  private var dismissed = false
  private var timer: Timer?
  private var itemTypes = "none"
  private var extractedURL: String?
  private var entryMemory: UInt64?
  private var attemptError: String?
  override func viewDidLoad() {
    super.viewDidLoad()
    view.backgroundColor = .systemBackground
    let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? []).flatMap { $0.attachments ?? [] }
    itemTypes = String(providers.flatMap { $0.registeredTypeIdentifiers }.joined(separator: ",").prefix(2048))
    entryMemory = ShareDiagnostics.memoryBytes()
    ShareDiagnostics.event("extension_launched", "item_type=\(itemTypes) memory_bytes=\(entryMemory.map(String.init) ?? "unavailable")")
    do { try record("started") }
    catch { fail("Cannot write the share diagnostic trace: " + error.localizedDescription); return }
    timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: false) { [weak self] _ in self?.fail("Reading the shared link timed out. Please try again.") }
    load(Array(providers.prefix(32)), at: 0, typeIndex: 0)
  }
  private func record(_ outcome: String, error: String? = nil) throws {
    try ShareDiagnostics.append([
      "attempt_id": attemptID, "timestamp": Date().timeIntervalSince1970 * 1000,
      // iOS exposes no supported public API for the originating app's identity.
      "source_app": NSNull(), "item_type": itemTypes,
      "url_or_null": extractedURL as Any? ?? NSNull(), "outcome": outcome,
      "error_or_null": error as Any? ?? NSNull(),
      "duration_ms": (ProcessInfo.processInfo.systemUptime - started) * 1000,
      "container_id": ShareDiagnostics.containerID,
      "memory_entry_bytes": entryMemory as Any? ?? NSNull(),
      "memory_exit_bytes": ShareDiagnostics.memoryBytes() as Any? ?? NSNull()
    ])
  }
  private func load(_ providers: [NSItemProvider], at index: Int, typeIndex: Int) {
    guard !finished else { return }
    guard index < providers.count else { fail("No Instagram, TikTok, or YouTube video link was found."); return }
    let provider = providers[index]
    let types = [UTType.url.identifier, UTType.plainText.identifier].filter { provider.hasItemConformingToTypeIdentifier($0) }
    guard typeIndex < types.count else { load(providers, at: index + 1, typeIndex: 0); return }
    provider.loadItem(forTypeIdentifier: types[typeIndex], options: nil) { [weak self] item, error in
      DispatchQueue.main.async {
        guard let self, !self.finished else { return }
        if let error { ShareDiagnostics.event("item_load_failed", error.localizedDescription) }
        let text = (item as? URL)?.absoluteString ?? (item as? String) ?? ""
        guard let url = SharedURL.extract(text) else {
          ShareDiagnostics.event("url_not_found", "item_type=\(types[typeIndex])")
          self.load(providers, at: index, typeIndex: typeIndex + 1); return
        }
        self.extractedURL = url.absoluteString
        ShareDiagnostics.event("url_extracted", "host=\(url.host ?? "unknown")")
        do {
          try QueueStore().enqueue(url: url.absoluteString, timestamp: Date().timeIntervalSince1970 * 1000)
          try self.record("saved")
          self.finished = true; self.timer?.invalidate()
          self.presentMessage("Saved on this device", failure: false)
          DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.dismissAttempt("saved") }
        } catch { self.fail("Could not finish saving this link: " + error.localizedDescription) }
      }
    }
  }
  private func fail(_ message: String) {
    guard !finished else { return }
    finished = true; timer?.invalidate()
    attemptError = message
    ShareDiagnostics.event("extension_failed", message)
    do { try record("failed", error: message) }
    catch { ShareDiagnostics.event("attempt_log_write_failed", error.localizedDescription) }
    presentMessage(message, failure: true)
    DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.dismissAttempt("failed") }
  }
  private func presentMessage(_ message: String, failure: Bool) {
    view.subviews.forEach { $0.removeFromSuperview() }
    let label = UILabel(); label.text = message; label.numberOfLines = 0; label.textAlignment = .center
    label.font = .preferredFont(forTextStyle: .body); label.adjustsFontForContentSizeCategory = true
    label.textColor = failure ? .systemRed : .label
    let stack = UIStackView(arrangedSubviews: [label]); stack.axis = .vertical; stack.spacing = 16
    if failure {
      let button = UIButton(type: .system); button.setTitle("Close", for: .normal)
      button.addTarget(self, action: #selector(close), for: .touchUpInside); stack.addArrangedSubview(button)
    }
    stack.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(stack)
    NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24), stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24), stack.centerYAnchor.constraint(equalTo: view.centerYAnchor)])
    stack.alpha = 0
    UIView.animate(withDuration: NativeMotion.quick, delay: 0, options: [.curveEaseOut, .beginFromCurrentState]) { stack.alpha = 1 }
    UIAccessibility.post(notification: .announcement, argument: message)
  }
  private func dismissAttempt(_ outcome: String) {
    guard !dismissed else { return }; dismissed = true
    do { try record(outcome, error: attemptError) }
    catch { ShareDiagnostics.event("exit_trace_failed", error.localizedDescription) }
    ShareDiagnostics.event("extension_dismiss", "outcome=\(outcome) duration_ms=\((ProcessInfo.processInfo.systemUptime - started) * 1000) memory_bytes=\(ShareDiagnostics.memoryBytes().map(String.init) ?? "unavailable")")
    extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
  }
  @objc private func close() { dismissAttempt("failed") }
}
