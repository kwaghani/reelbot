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
  let diagnostics = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); diagnostics.name = "Build 30 share diagnostics"; diagnostics.lifetime = .keepAlways; add(diagnostics)
  app.buttons["Close diagnostics"].tap()
  let images=app.buttons["Image diagnostics"]
  XCTAssertTrue(images.waitForExistence(timeout:10));images.tap()
  XCTAssertTrue(app.staticTexts["Image diagnostics"].waitForExistence(timeout:10))
  let imageDiagnostics=XCTAttachment(screenshot:XCUIScreen.main.screenshot());imageDiagnostics.name="Image diagnostics on phone";imageDiagnostics.lifetime = .keepAlways;add(imageDiagnostics)
  app.buttons["Back"].firstMatch.tap()
  app.descendants(matching: .any).matching(identifier: "Recent").firstMatch.tap()
  let bcd=app.buttons["BCD Tofu House"].firstMatch
  for _ in 0..<5 { if bcd.isHittable { break }; app.swipeUp() }
  XCTAssertTrue(bcd.isHittable);bcd.tap()
  let photo=app.images["Photo of BCD Tofu House"].firstMatch
  let found=photo.waitForExistence(timeout:70)
  if !found { let evidence=XCTAttachment(screenshot:XCUIScreen.main.screenshot());evidence.name="Photo check state";evidence.lifetime = .keepAlways;add(evidence) }
  XCTAssertTrue(found)
  let loaded=XCTNSPredicateExpectation(predicate:NSPredicate(format:"value == 'Loaded'"),object:photo)
  XCTAssertEqual(XCTWaiter.wait(for:[loaded],timeout:10),.completed)
  Thread.sleep(forTimeInterval:3)
  let bcdImage=XCTAttachment(screenshot:XCUIScreen.main.screenshot());bcdImage.name="BCD real photograph";bcdImage.lifetime = .keepAlways;add(bcdImage)
  app.buttons["Edit"].firstMatch.tap()
  let retry=app.buttons["Retry image"].firstMatch
  for _ in 0..<6 { if retry.isHittable { break }; app.swipeUp() }
  XCTAssertTrue(retry.isHittable);retry.tap();Thread.sleep(forTimeInterval:2)
  let retried=XCTAttachment(screenshot:XCUIScreen.main.screenshot());retried.name="Retry image action";retried.lifetime = .keepAlways;add(retried)
 }
}
