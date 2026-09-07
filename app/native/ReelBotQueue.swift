import Foundation
import React

@objc(ReelBotQueue)
final class ReelBotQueue: NSObject {
  @objc static func requiresMainQueueSetup() -> Bool { false }
  @objc func constantsToExport() -> [AnyHashable: Any] {
#if DEBUG
    return ["debugFeaturesEnabled": true]
#else
    return ["debugFeaturesEnabled": false]
#endif
  }
  @objc(enqueue:timestamp:resolver:rejecter:)
  func enqueue(_ url: String, timestamp: Double, resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    do { resolver(try QueueStore().enqueue(url: url, timestamp: timestamp)) }
    catch { rejecter("queue_write", "Could not save this link on the device.", error) }
  }
  @objc(read:rejecter:)
  func read(_ resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    do { resolver(try QueueStore().read()) }
    catch { rejecter("queue_read", "Could not read pending links. Please retry.", error) }
  }
  @objc(acknowledge:resolver:rejecter:)
  func acknowledge(_ id: String, resolver: RCTPromiseResolveBlock, rejecter: RCTPromiseRejectBlock) {
    do { try QueueStore().acknowledge(id: id); resolver(nil) }
    catch { rejecter("queue_ack", "Could not finish reading a saved link.", error) }
  }
}
