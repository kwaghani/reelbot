import XCTest
final class VenueIdentityUITests: XCTestCase {
 let app=XCUIApplication(bundleIdentifier:"com.krishwaghani.reelbot")
 override func setUpWithError() throws { continueAfterFailure=false;app.activate();if app.buttons["Close"].exists{app.buttons["Close"].tap()};if app.buttons["Cancel"].exists{app.buttons["Cancel"].tap()};XCTAssertTrue(app.buttons["Search saved entries"].waitForExistence(timeout:45)) }
 func capture(_ name:String){let a=XCTAttachment(screenshot:XCUIScreen.main.screenshot());a.name=name;a.lifetime = .keepAlways;add(a)}
 func open(_ title:String){app.buttons["Search saved entries"].tap();app.textFields["Search your saves"].typeText(title);let item=app.buttons[title].firstMatch;XCTAssertTrue(item.waitForExistence(timeout:12));item.tap();Thread.sleep(forTimeInterval:2)}
 func text(_ label:String)->XCUIElement{app.staticTexts.matching(NSPredicate(format:"label ==[c] %@",label)).firstMatch}
 func reveal(_ label:String){for _ in 0..<3{if text(label).isHittable{return};app.swipeUp();Thread.sleep(forTimeInterval:0.5)}}
 func testRestaurantSchema(){open("BCD Tofu House");reveal("Cuisine");XCTAssertTrue(text("Cuisine").exists);XCTAssertTrue(text("Korean").exists);XCTAssertFalse(text("Difficulty").exists);XCTAssertFalse(text("Not specified").exists);capture("Restaurant attributes")}
 func testTrailSchema(){open("Sturtevant Falls Trail");reveal("Activity");XCTAssertTrue(text("Activity").exists);XCTAssertTrue(text("hike").exists);XCTAssertFalse(text("Cuisine").exists);XCTAssertFalse(text("Price level").exists);XCTAssertFalse(text("Not specified").exists);capture("Trail attributes")}
 func testHotelSchema(){open("Hotel schema demo");reveal("Nightly rate");XCTAssertTrue(text("Nightly rate").exists);XCTAssertTrue(text("Amenities").exists);XCTAssertFalse(text("Cuisine").exists);XCTAssertFalse(text("Not specified").exists);capture("Hotel attributes")}
}
