import XCTest
final class ReelBotMotionReleaseReview: XCTestCase {
 func testNormalLibrarySyncAndDiagnostics() {
  continueAfterFailure = false
  let app = XCUIApplication(bundleIdentifier: "com.krishwaghani.reelbot")
  app.launch()
  XCTAssertTrue(app.buttons["Save a reel"].waitForExistence(timeout: 20))
  Thread.sleep(forTimeInterval: 2)
  let recent = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); recent.name = "Normal Release library"; recent.lifetime = .keepAlways; add(recent)
  app.descendants(matching: .any).matching(identifier: "Settings").firstMatch.tap()
  let sync = app.buttons["Sync now"]
  XCTAssertTrue(sync.waitForExistence(timeout: 10)); sync.tap()
  XCTAssertTrue(app.staticTexts["Synced just now"].waitForExistence(timeout: 45))
  let synced = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); synced.name = "Normal Release synced"; synced.lifetime = .keepAlways; add(synced)
  let version = app.buttons["ReelBot version"]
  for _ in 0..<6 { if version.isHittable { break }; app.swipeUp() }
  XCTAssertTrue(version.isHittable)
  for _ in 0..<5 { version.tap() }
  XCTAssertTrue(app.buttons["Close diagnostics"].waitForExistence(timeout: 10))
  XCTAssertTrue(app.staticTexts["Container reachable: Yes"].waitForExistence(timeout: 10))
  let diagnostics = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); diagnostics.name = "Build 29 share diagnostics"; diagnostics.lifetime = .keepAlways; add(diagnostics)
  app.buttons["Close diagnostics"].tap()
  app.descendants(matching: .any).matching(identifier: "Recent").firstMatch.tap()
 }
}
