import XCTest

final class OrganizationUITests: XCTestCase {
    let app = XCUIApplication(bundleIdentifier: "com.krishwaghani.reelbot")
    override func setUpWithError() throws { continueAfterFailure = false; app.launch(); XCTAssertTrue(app.buttons["Search saved entries"].waitForExistence(timeout: 30)) }
    func capture(_ name: String) { let shot = XCTAttachment(screenshot: app.screenshot()); shot.name = name; shot.lifetime = .keepAlways; add(shot) }
    func testSearchAndCollapsedTitle() {
        app.buttons["Search saved entries"].tap()
        XCTAssertTrue(app.textFields["Search your saves"].waitForExistence(timeout: 5))
        capture("Search expanded by tap")
        app.buttons["Cancel"].tap()
        XCTAssertFalse(app.textFields["Search your saves"].exists)
        let start = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.27))
        let end = app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.65))
        start.press(forDuration: 0.1, thenDragTo: end)
        XCTAssertTrue(app.textFields["Search your saves"].waitForExistence(timeout: 5))
        capture("Search expanded by pull down")
        app.buttons["Cancel"].tap()
        app.swipeUp()
        let titles = app.descendants(matching: .any).matching(identifier: "Saved").allElementsBoundByIndex.filter { $0.frame.minY > 30 && $0.frame.maxY < 100 }
        XCTAssertEqual(titles.count, 1)
        XCTAssertLessThan(titles[0].frame.minY, 100)
        capture("Saved title collapsed")
    }
    func testFolderSwipeAndLongPress() {
        let all = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "All folders")).firstMatch
        if !all.isHittable { app.scrollViews.element(boundBy: 2).swipeLeft() }
        XCTAssertTrue(all.exists)
        all.tap()
        let places = app.buttons["Places, 16 entries"]
        XCTAssertTrue(places.waitForExistence(timeout: 5))
        places.swipeLeft()
        XCTAssertTrue(app.buttons["Hide Places"].waitForExistence(timeout: 5))
        capture("Folder swipe Hide")
        app.buttons["Hide Places"].tap()
        XCTAssertFalse(places.exists)
        let hidden = app.buttons["Show hidden folders"]
        XCTAssertTrue(hidden.waitForExistence(timeout: 5)); hidden.tap()
        let hiddenPlace = app.buttons["Places, 16 entries, hidden"]
        XCTAssertTrue(hiddenPlace.waitForExistence(timeout: 5)); hiddenPlace.press(forDuration: 1)
        XCTAssertTrue(app.buttons["Show"].waitForExistence(timeout: 5))
        capture("Folder long press Show")
        app.buttons["Show"].tap()
        XCTAssertTrue(places.waitForExistence(timeout: 5)); places.press(forDuration: 1)
        XCTAssertTrue(app.buttons["Hide"].waitForExistence(timeout: 5))
        capture("Folder long press Hide")
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.75)).tap()
    }
    func testHiddenCityFolderCanBeRestored() {
        let all = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "All folders")).firstMatch
        if !all.isHittable { app.scrollViews.element(boundBy: 2).swipeLeft() }
        all.tap()
        app.buttons["Places, 16 entries"].tap()
        let city = app.buttons["Los Angeles, 16 entries"]
        for _ in 0..<12 { if city.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(city.isHittable)
        city.press(forDuration: 1)
        app.buttons["Hide"].tap()
        let show = app.buttons["Show hidden folders"]
        XCTAssertTrue(show.waitForExistence(timeout: 5)); show.tap()
        let hiddenCity = app.buttons["Los Angeles, 16 entries, hidden"]
        XCTAssertTrue(hiddenCity.waitForExistence(timeout: 5)); hiddenCity.press(forDuration: 1)
        app.buttons["Show"].tap()
        XCTAssertTrue(city.waitForExistence(timeout: 5)); capture("Hidden city restored")
    }
}
