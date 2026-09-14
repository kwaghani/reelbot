import XCTest
final class ReelBotMotionReview: XCTestCase {
 let app = XCUIApplication(bundleIdentifier: "com.krishwaghani.reelbot")
 override func setUpWithError() throws {
  continueAfterFailure = false
  app.launchArguments = ["--reelbot-visual-fixture"]
  app.launchEnvironment = ["REELBOT_FIXTURE_COUNT": "240"]
  app.launch()
  Thread.sleep(forTimeInterval: 2); capture("Launch");
  XCTAssertTrue(app.buttons["Search saved entries"].waitForExistence(timeout: 10))
 }
 func tab(_ name: String) { app.descendants(matching: .any).matching(identifier: name).firstMatch.tap() }
 func capture(_ name: String) { let a = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a) }
 func pause() { Thread.sleep(forTimeInterval: 1) }
 func testA_TabSwitch() {
  tab("Map"); pause(); capture("Map tab"); tab("Settings"); pause(); capture("Settings tab"); tab("Recent"); pause(); capture("Recent tab 240 entries")
 }
 func testB_CardDetailAndDrag() {
  let card = app.buttons["Tartine"].firstMatch; XCTAssertTrue(card.waitForExistence(timeout: 10)); card.tap(); pause(); capture("Card detail")
  XCTAssertTrue(app.buttons["Close"].exists)
  let top = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.15))
  top.press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.55)))
  pause(); capture("Sheet lower snap")
  if app.buttons["Close"].exists { app.buttons["Close"].tap() }
  XCTAssertTrue(app.buttons["Search saved entries"].waitForExistence(timeout: 10)); capture("Detail dismissed")
 }
 func testC_FolderPushBack() {
  app.buttons["Open Places"].tap(); pause(); capture("Places folder")
  XCTAssertTrue(app.buttons["Back"].exists)
  app.coordinate(withNormalizedOffset: CGVector(dx: 0.01, dy: 0.5)).press(forDuration: 0.1, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)))
  XCTAssertTrue(app.buttons["Search saved entries"].waitForExistence(timeout: 10)); capture("Interactive back")
 }
 func testD_FilterAndSearch() {
  app.buttons["Needs review 1"].tap(); pause(); capture("Review filter")
  XCTAssertTrue(app.buttons["Tartine"].exists)
  app.buttons["Needs review 1"].tap(); app.buttons["Search saved entries"].tap()
  let field = app.textFields["Search your saves"]; XCTAssertTrue(field.waitForExistence(timeout: 10)); field.typeText("Tartine"); pause(); capture("Debounced search")
  app.buttons["Cancel"].tap(); pause(); capture("Search collapsed")
 }
 func testE_MapMarkerSheet() {
  tab("Map"); pause(); capture("Clustered map")
  let cluster = app.descendants(matching: .any).matching(identifier: "80 saved entries in San Francisco").firstMatch
  XCTAssertTrue(cluster.waitForExistence(timeout: 10)); cluster.tap(); pause(); capture("Cluster expansion")
  let marker = app.descendants(matching: .any).matching(identifier: "Tartine").firstMatch
  XCTAssertTrue(marker.waitForExistence(timeout: 10)); marker.tap(); pause(); capture("Marker to detail sheet")
  XCTAssertTrue(app.buttons["Close"].exists); app.buttons["Close"].tap()
 }
 func testF_VerificationAccept() {
  app.buttons["Needs review 1"].tap(); pause(); app.buttons["Check the venue name"].firstMatch.tap(); pause(); capture("Verification expanded")
  let choice = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Tartine' AND label CONTAINS 'San Francisco'")).firstMatch
  XCTAssertTrue(choice.waitForExistence(timeout: 10)); choice.tap(); pause(); capture("Verification accepted")
  XCTAssertFalse(app.buttons["Check the venue name"].exists)
 }
 func testG_LargeLibraryScroll() {
  for _ in 0..<8 { app.swipeUp(velocity: .fast) }
  pause(); capture("240-entry list scrolled")
  tab("Recent"); pause(); XCTAssertTrue(app.buttons["Tartine"].isHittable); capture("Active tab scroll to top")
 }
}
final class ReelBotReducedMotionReview: XCTestCase {
 func testReducedMotionOnDevice() {
  let app = XCUIApplication(bundleIdentifier: "com.krishwaghani.reelbot")
  app.launchArguments = ["--reelbot-visual-fixture"]; app.launchEnvironment = ["REELBOT_FIXTURE_COUNT": "240"]; app.launch()
  let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences"); settings.activate()
  Thread.sleep(forTimeInterval: 1)
  for _ in 0..<5 { let back = settings.navigationBars.buttons.firstMatch; if settings.staticTexts["Accessibility"].isHittable { break }; if back.exists && back.label != "Edit" && back.label != "Search" { back.tap() } else { break } }
  var accessibility = settings.staticTexts["Accessibility"].firstMatch
  for _ in 0..<6 { if accessibility.isHittable { break }; settings.swipeUp(); accessibility = settings.staticTexts["Accessibility"].firstMatch }
  guard accessibility.isHittable else { print(settings.debugDescription); XCTFail("Accessibility settings unavailable"); return }; accessibility.tap()
  let motion = settings.staticTexts["Motion"].firstMatch
  guard motion.waitForExistence(timeout: 5) else { print(settings.debugDescription); XCTFail("Motion settings unavailable"); return }; motion.tap()
  let toggle = settings.switches["REDUCE_MOTION"].firstMatch
  guard toggle.waitForExistence(timeout: 5) else { print(settings.debugDescription); XCTFail("Reduce Motion switch unavailable"); return }
  let original = toggle.value as? String
  guard original == "0" || original == "1" else { XCTFail("Cannot read system Reduce Motion state"); return }
  defer { settings.activate(); if toggle.value as? String != original { toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap() }; app.activate() }
  if original != "1" { toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap() }
  let enabled = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == '1'"), object: toggle)
  guard XCTWaiter.wait(for: [enabled], timeout: 5) == .completed else { XCTFail("System Reduce Motion did not turn on"); return }
  Thread.sleep(forTimeInterval: 0.5)
  let state = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); state.name = "System Reduce Motion enabled"; state.lifetime = .keepAlways; add(state)
  app.activate(); Thread.sleep(forTimeInterval: 1)
  for name in ["Map", "Settings", "Recent"] { app.descendants(matching: .any).matching(identifier: name).firstMatch.tap(); Thread.sleep(forTimeInterval: 1) }
  let card = app.buttons["Tartine"].firstMatch
  if card.isHittable { card.tap(); Thread.sleep(forTimeInterval: 1); let a = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); a.name = "Reduced motion detail"; a.lifetime = .keepAlways; add(a); if app.buttons["Close"].exists { app.buttons["Close"].tap() } }

 }
}
