import UIKit
import UniformTypeIdentifiers
import os.log

final class ShareExtensionViewController: UIViewController {
  private let started = ProcessInfo.processInfo.systemUptime
  private var attempted = false
  override func viewDidLoad() {
    super.viewDidLoad()
    view.backgroundColor = UIColor(red: 0.96, green: 0.95, blue: 0.92, alpha: 1)
    guard !attempted else { return }; attempted = true
    let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? []).flatMap { $0.attachments ?? [] }
    load(providers, at: 0)
  }
  private func load(_ providers: [NSItemProvider], at index: Int) {
    guard index < providers.count else { showError("No Instagram, TikTok, or YouTube video link was found."); return }
    let provider = providers[index]
    let type = provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) ? UTType.url.identifier : UTType.plainText.identifier
    guard provider.hasItemConformingToTypeIdentifier(type) else { load(providers, at: index + 1); return }
    provider.loadItem(forTypeIdentifier: type, options: nil) { [weak self] item, error in
      guard let self else { return }
      let text = (item as? URL)?.absoluteString ?? (item as? String) ?? ""
      let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)
      let range = NSRange(text.startIndex..., in: text)
      let links = detector?.matches(in: text, range: range).compactMap { $0.url } ?? []
      let supported = links.first { url in
        guard url.scheme == "https" || url.scheme == "http", url.user == nil, url.password == nil, url.port == nil else { return false }
        let host = (url.host ?? "").lowercased(); let path = url.path
        if ["instagram.com", "www.instagram.com", "m.instagram.com"].contains(host) { return path.range(of: "^/(reel|reels|p)/[A-Za-z0-9_-]+/?$", options: .regularExpression) != nil }
        if ["vm.tiktok.com", "vt.tiktok.com"].contains(host) { return path.range(of: "^/[A-Za-z0-9]+/?$", options: .regularExpression) != nil }
        if ["tiktok.com", "www.tiktok.com", "m.tiktok.com"].contains(host) { return path.range(of: "^/(@[A-Za-z0-9_.-]+/video/[0-9]+|t/[A-Za-z0-9]+)/?$", options: .regularExpression) != nil }
        if host == "youtu.be" { return path.range(of: "^/[A-Za-z0-9_-]{11}/?$", options: .regularExpression) != nil }
        if ["youtube.com", "www.youtube.com", "m.youtube.com"].contains(host) {
          return path.range(of: "^/shorts/[A-Za-z0-9_-]{11}/?$", options: .regularExpression) != nil || (path == "/watch" && URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "v" })?.value?.count == 11)
        }
        return false
      }
      guard let url = supported else { DispatchQueue.main.async { self.load(providers, at: index + 1) }; return }
      do {
        let writeStart = ProcessInfo.processInfo.systemUptime
        try QueueStore().enqueue(url: url.absoluteString, timestamp: Date().timeIntervalSince1970 * 1000)
        let elapsed = (ProcessInfo.processInfo.systemUptime - self.started) * 1000
        os_log("queue_write_ms=%.3f completion_ms=%.3f", log: OSLog(subsystem: "com.krishwaghani.reelbot.share", category: "timing"), type: .info, (ProcessInfo.processInfo.systemUptime - writeStart) * 1000, elapsed)
        DispatchQueue.main.async { self.extensionContext?.completeRequest(returningItems: [], completionHandler: nil) }
      } catch { DispatchQueue.main.async { self.showError("Could not save this link. Please try again.") } }
    }
  }
  private func showError(_ message: String) {
    let label = UILabel(); label.text = message; label.numberOfLines = 0; label.textAlignment = .center
    let button = UIButton(type: .system); button.setTitle("Close", for: .normal); button.addTarget(self, action: #selector(close), for: .touchUpInside)
    let stack = UIStackView(arrangedSubviews: [label, button]); stack.axis = .vertical; stack.spacing = 16; stack.translatesAutoresizingMaskIntoConstraints = false
    view.addSubview(stack)
    NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24), stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24), stack.centerYAnchor.constraint(equalTo: view.centerYAnchor)])
  }
  @objc private func close() { extensionContext?.completeRequest(returningItems: [], completionHandler: nil) }
}
