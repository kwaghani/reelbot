import XCTest
final class ImageryUITests: XCTestCase {
 let app = XCUIApplication(bundleIdentifier: "com.krishwaghani.reelbot")
 override func setUpWithError() throws { continueAfterFailure = false; app.activate(); if app.buttons["Close"].exists { app.buttons["Close"].tap() }; if app.buttons["Cancel"].exists { app.buttons["Cancel"].tap() }; XCTAssertTrue(app.buttons["Save a reel"].waitForExistence(timeout:45)) }
 func capture(_ name:String) { let a=XCTAttachment(screenshot:XCUIScreen.main.screenshot());a.name=name;a.lifetime = .keepAlways;add(a) }
 func testRecentScroll() {
  Thread.sleep(forTimeInterval:2); capture("Recent top")
  let first=app.buttons["BCD Tofu House"].firstMatch
  if first.exists { let frame=first.frame; Thread.sleep(forTimeInterval:8); XCTAssertEqual(frame,first.frame,"Imagery must not move the card") }
  capture("Recent loaded")
  for n in 1...4 { app.swipeUp(); Thread.sleep(forTimeInterval:1); capture("Scroll \(n)") }
  for _ in 0..<5 { app.swipeDown() }
 }
 func testGallerySwap() {
  app.buttons["Search saved entries"].tap(); app.textFields["Search your saves"].typeText("BCD Tofu House")
  let entry=app.buttons["BCD Tofu House"].firstMatch; XCTAssertTrue(entry.waitForExistence(timeout:10));entry.tap()
  let swap=app.buttons["Change photo"]; XCTAssertTrue(swap.waitForExistence(timeout:65)); capture("BCD dish and gallery")
  swap.tap(); Thread.sleep(forTimeInterval:3);capture("BCD selected venue photo")
  XCTAssertTrue(app.descendants(matching:.any).matching(NSPredicate(format:"label == %@", "Google Maps")).firstMatch.exists)
  let cover=app.buttons["Use reel cover"];XCTAssertTrue(cover.exists);cover.tap();Thread.sleep(forTimeInterval:2);capture("BCD dish choice restored")
  XCTAssertTrue(app.descendants(matching:.any).matching(NSPredicate(format:"label == %@", "Creator’s reel")).firstMatch.exists)
  app.buttons["Close"].tap();app.buttons["Cancel"].tap()
 }
 func testPersistedDishAfterRestart() {
  app.buttons["Search saved entries"].tap();app.textFields["Search your saves"].typeText("BCD Tofu House")
  let entry=app.buttons["BCD Tofu House"].firstMatch;XCTAssertTrue(entry.waitForExistence(timeout:10));entry.tap()
  XCTAssertTrue(app.descendants(matching:.any).matching(NSPredicate(format:"label == %@", "Creator’s reel")).firstMatch.waitForExistence(timeout:65))
  capture("Dish choice after restart");app.buttons["Close"].tap();app.buttons["Cancel"].tap()
 }
 func testVenueAttribution() {
  app.buttons["Search saved entries"].tap();app.textFields["Search your saves"].typeText("Vees Cafe")
  let entry=app.buttons["Vees Cafe"].firstMatch;XCTAssertTrue(entry.waitForExistence(timeout:10));entry.tap()
  XCTAssertTrue(app.descendants(matching:.any).matching(NSPredicate(format:"label == %@", "Google Maps")).firstMatch.waitForExistence(timeout:65));capture("Vees attribution")
  XCTAssertFalse(app.buttons["Use reel cover"].exists)
  app.buttons["Close"].tap();app.buttons["Cancel"].tap()
 }
}
