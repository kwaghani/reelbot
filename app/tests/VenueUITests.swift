import XCTest
import UIKit
final class VenueUITests: XCTestCase {
    let app = XCUIApplication(bundleIdentifier: "com.krishwaghani.reelbot")
    override func setUpWithError() throws { continueAfterFailure = false }
    func capture(_ name: String) { Thread.sleep(forTimeInterval: 0.7); let a = XCTAttachment(screenshot: app.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a) }
    func launch(_ count: Int = 50) {
        app.launchArguments = ["--reelbot-visual-fixture"]
        app.launchEnvironment["REELBOT_FIXTURE_COUNT"] = String(count)
        app.launch()
        XCTAssertTrue(app.buttons["Save a reel"].waitForExistence(timeout: 45))
    }
    func tab(_ name: String) { let target = app.descendants(matching: .any).matching(identifier: name).firstMatch; XCTAssertTrue(target.waitForExistence(timeout: 8)); target.tap() }
    func button(_ prefix: String) -> XCUIElement { app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", prefix)).firstMatch }
    func reveal(_ element: XCUIElement) { for _ in 0..<18 { if element.isHittable { return }; app.swipeUp() }; XCTAssertTrue(element.isHittable) }
    func closeSheet() { app.buttons["Close"].tap(); Thread.sleep(forTimeInterval: 0.8); if app.buttons["Close"].isHittable { app.buttons["Close"].tap(); Thread.sleep(forTimeInterval: 0.8) } }
    func testOriginalMap() {
        app.launch(); XCTAssertTrue(app.buttons["Save a reel"].waitForExistence(timeout: 30)); app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.94)).tap(); capture("Original map build 23")
    }
    func testSurfaceInventory() {
        launch(); capture("Recent grid")
        app.buttons["Save a reel"].tap(); capture("Save reel"); closeSheet()
        app.buttons["Search saved entries"].tap(); app.textFields["Search your saves"].typeText("Bar Flores"); capture("Search bar"); app.buttons["Cancel"].tap()
        app.buttons["Bar Flores"].tap(); capture("Bar detail"); app.buttons["Edit"].tap(); capture("Entry editor"); closeSheet()
        app.buttons["Saved options"].tap(); app.buttons["New folder"].tap(); capture("Folder editor"); closeSheet()
        launch(); tab("Map"); capture("Map clusters")
        app.descendants(matching:.any).matching(identifier:"List view").firstMatch.tap(); capture("Map list")
        tab("Settings"); capture("Settings top"); app.swipeUp(); capture("Settings bottom")
        launch(); app.buttons["Open Places"].tap(); capture("Places city grouping")
        app.buttons["Type"].tap(); capture("Places type grouping"); let bars=button("Bars,"); reveal(bars); capture("Type folders"); bars.tap(); capture("Bar folder")
        let la=button("Los Angeles"); if la.exists { la.tap(); capture("Bar city filter") }
    }
    func testMarkerAtlas() { launch(16); tab("Map"); capture("Sixteen venue markers") }
    func testSeventeenthKind() {
        launch(17); app.buttons["Search saved entries"].tap(); app.textFields["Search your saves"].typeText("Observatory"); capture("Seventeenth card badge"); app.buttons["Cancel"].tap()
        let browse=button("Browse folders"); reveal(browse); browse.tap(); button("Places,").tap(); app.buttons["Type"].tap(); let folder=button("Observatories,"); reveal(folder); capture("Seventeenth folder icon"); folder.tap(); capture("Seventeenth folder entry")
        launch(17); tab("Map"); let chip=button("Observatories "); for _ in 0..<12 { if chip.isHittable { break }; app.scrollViews.firstMatch.swipeLeft() }; XCTAssertTrue(chip.isHittable); chip.tap(); capture("Seventeenth marker and chip")
        app.descendants(matching:.any).matching(identifier:"List view").firstMatch.tap(); XCTAssertTrue(button("Observatory saved place").exists); capture("Seventeenth list")
    }
    func testAdditionalScreens() {
        launch(0); capture("Empty Recent")
        launch(4); capture("Small Recent")
        launch(); tab("Groups"); capture("Debug interest view"); tab("Recent")
        app.buttons["Saved options"].tap(); app.buttons["Select entries"].tap(); app.buttons["Bar Flores"].tap(); app.buttons["Copy"].tap(); capture("Copy entries"); closeSheet()
    }
    func testMapPermissionsAndUnits() {
        launch(16); tab("Map"); XCTAssertTrue(app.buttons["Distance filter"].waitForExistence(timeout: 10)); capture("Map permission and region"); app.buttons["Distance filter"].tap(); capture("Regional distance options"); app.coordinate(withNormalizedOffset: CGVector(dx: 0.05, dy: 0.5)).tap()
        app.descendants(matching:.any).matching(identifier:"List view").firstMatch.tap(); capture("Distances for device region")
    }
    func testGrayscaleAtlas() {
        app.launchEnvironment["REELBOT_FIXTURE_GRAYSCALE"] = "1"; launch(16); tab("Map"); capture("Sixteen grayscale venue markers"); app.launchEnvironment.removeValue(forKey:"REELBOT_FIXTURE_GRAYSCALE")
    }
    func testAccessibilityScreens() {
        launch(4); capture("Large type Recent"); app.buttons["Attraction saved place"].tap(); capture("Large type detail"); app.buttons["Edit"].tap(); capture("Large type editor"); closeSheet()
        launch(4); tab("Map"); capture("Large type map"); app.descendants(matching:.any).matching(identifier:"List view").firstMatch.tap(); capture("Large type map list")
        tab("Settings"); capture("Large type Settings"); app.swipeUp(); capture("Large type Settings scrolled")
        launch(4); app.buttons["Save a reel"].tap(); capture("Large type save sheet"); closeSheet()
        launch(-1); app.buttons["Add venue or topic"].tap(); capture("Large type source details"); closeSheet()
        launch(0); capture("Large type empty")
    }
    func testDeniedMap() { launch(16); tab("Map"); Thread.sleep(forTimeInterval: 1); XCTAssertFalse(app.buttons["Distance filter"].exists); capture("Location denied map"); app.descendants(matching:.any).matching(identifier:"List view").firstMatch.tap(); capture("Location denied list") }
    func testClusterInteraction() {
        launch(); tab("Map"); capture("Fifty places clustered")
        let tree=XCTAttachment(string:app.debugDescription); tree.name="Map accessibility hierarchy"; tree.lifetime = .keepAlways; add(tree)
        for _ in 0..<8 {
            let cluster=app.descendants(matching:.any).matching(NSPredicate(format:"label MATCHES %@", "[0-9]+ saved entries")).firstMatch
            if !cluster.exists { break }
            cluster.tap(); Thread.sleep(forTimeInterval: 0.8)
            if app.buttons["Close"].exists { break }
        }
        capture("Cluster expanded")
        XCTAssertTrue(app.buttons["Close"].exists)
    }
    func testVenueOverride() {
        launch(4); app.buttons["Bar Flores"].tap(); app.buttons["Edit"].tap(); let cafe=app.buttons["Café"]; reveal(cafe); cafe.tap(); let save=app.buttons["Save"]; reveal(save); save.tap(); capture("User venue override")
        for _ in 0..<8 { app.swipeDown(); if app.buttons["Edit"].isHittable { break } }
        XCTAssertTrue(app.staticTexts["Cafe"].exists)
        app.buttons["Edit"].tap(); reveal(app.buttons["Café"]); XCTAssertTrue(app.buttons["Café"].isSelected); capture("User override retained in editor")
    }
    func testOneVenueAcrossSurfaces() {
        launch(4); app.buttons["Search saved entries"].tap(); app.textFields["Search your saves"].typeText("Bar Flores"); capture("Bar card badge"); app.buttons["Cancel"].tap()
        app.buttons["Bar Flores"].tap(); capture("Same bar detail"); closeSheet()
        launch(4); tab("Map"); let bars=button("Bars "); XCTAssertTrue(bars.isHittable); bars.tap(); capture("Same bar marker and chip")
        app.descendants(matching:.any).matching(identifier:"Bar Flores").firstMatch.tap(); capture("Selected bar marker and detail"); closeSheet()
        launch(4); let browse=button("Browse folders"); reveal(browse); browse.tap(); button("Places,").tap(); app.buttons["Type"].tap(); let folder=button("Bars,"); reveal(folder); app.swipeUp(); capture("Same bar folder icon"); folder.tap(); capture("Same bar inside folder")
    }
    func testExtraAccessibilitySurfaces() {
        launch(4); app.buttons["Search saved entries"].tap(); app.textFields["Search your saves"].typeText("Bar Flores"); capture("Large type search"); app.buttons["Cancel"].tap()
        app.buttons["Saved options"].tap(); app.buttons["New folder"].tap(); capture("Large type folder editor"); closeSheet()
        launch(4); let browse=button("Browse folders"); reveal(browse); browse.tap(); capture("Large type all folders"); button("Places,").tap(); capture("Large type Places city"); app.buttons["Type"].tap(); let bars=button("Bars,"); reveal(bars); capture("Large type venue folders"); bars.tap(); capture("Large type bar folder")
        launch(4); app.buttons["Saved options"].tap(); app.buttons["Select entries"].tap(); let entry=app.buttons["Bar Flores"]; reveal(entry); entry.tap(); app.buttons["Copy"].tap(); capture("Large type copy entries"); closeSheet()
        launch(4); tab("Groups"); capture("Large type Debug interest")
    }
    func testSourceInfoSheet() {
        launch(-1); app.buttons["Add venue or topic"].tap(); capture("Source information sheet")
    }
    func testReducedMotion() {
        XCTAssertTrue(UIAccessibility.isReduceMotionEnabled)
        launch(4); tab("Map"); app.descendants(matching:.any).matching(identifier:"Bar Flores").firstMatch.tap(); XCTAssertTrue(app.buttons["Edit"].exists); capture("Reduced motion detail")
    }
}
