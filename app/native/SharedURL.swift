import Foundation

enum SharedURL {
  static func extract(_ text: String) -> URL? {
    guard text.utf8.count <= 16384 else { return nil }
    let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)
    let range = NSRange(text.startIndex..., in: text)
    let links = detector?.matches(in: text, range: range).compactMap { $0.url } ?? []
    return links.first { url in
      guard url.scheme == "https" || url.scheme == "http", url.user == nil, url.password == nil, url.port == nil else { return false }
      let host = (url.host ?? "").lowercased(); let path = url.path
      if ["instagr.am", "www.instagr.am"].contains(host) && path.range(of: "^/[A-Za-z0-9_-]+/?$", options: .regularExpression) != nil { return true }
      if ["instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"].contains(host) { return path.range(of: "^/((reel|reels|p|tv)/[A-Za-z0-9_-]+|share/((reel|p)/)?[A-Za-z0-9_-]+)/?$", options: .regularExpression) != nil }
      if ["vm.tiktok.com", "vt.tiktok.com"].contains(host) { return path.range(of: "^/[A-Za-z0-9]+/?$", options: .regularExpression) != nil }
      if ["tiktok.com", "www.tiktok.com", "m.tiktok.com"].contains(host) { return path.range(of: "^/(@[A-Za-z0-9_.-]*/video/[0-9]+|t/[A-Za-z0-9]+)/?$", options: .regularExpression) != nil }
      if host == "youtu.be" { return path.range(of: "^/[A-Za-z0-9_-]{11}/?$", options: .regularExpression) != nil }
      if ["youtube.com", "www.youtube.com", "m.youtube.com"].contains(host) {
        return path.range(of: "^/shorts/[A-Za-z0-9_-]{11}/?$", options: .regularExpression) != nil || (path == "/watch" && URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "v" })?.value?.count == 11)
      }
      return false
    }
  }
}
