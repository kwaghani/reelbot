# Native share verification

The extension accepts supported URL/plain-text items, writes one atomic queue file and completes its request. It loads Foundation/UIKit, not React Native. Its source contains no networking or authentication. Container failures display an error and Close button.

Both targets need the same App Group entitlement. Main-app acknowledgement occurs only after the SQLite commit; importing an unacknowledged file again remains idempotent. The native writer logs `queue_write_ms` and `completion_ms` through the system log.

Physical acceptance requires Instagram, TikTok and YouTube on a connected iPhone. For each host, measure presentation-to-completion including provider loading, verify under 400 ms, and repeat with the main app backgrounded and stopped. Test offline sharing/reconnect, 50 consecutive presentations, duplicate URLs and extension-process network attribution. A filesystem benchmark or static scan alone cannot pass these physical checks.

Current results are in `VERIFICATION.md`. The iPhone 15 Pro was unavailable for the addendum's host matrix and build-20 installation. The signed update is prepared. Build 19 was installed in the prior device test. The local API/worker and the test build require this Mac and the phone to be reachable on the same network. iOS may defer processing after force-quit until the main app is opened.
