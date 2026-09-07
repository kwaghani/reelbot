# Share verification

The extension accepts URL and plain-text items, finds a supported video link, writes one atomic queue file, and completes the extension request. It loads Foundation/UIKit, not a React Native runtime. Its source contains no networking, session check, or library selection. Container failures display a visible error with a Close button.

Both app and extension require the same App Group entitlement. An unsigned simulator build cannot access the container. Queue files are acknowledged only after the main app commits them to SQLite. Repeated imports normalize the URL and remain idempotent.

Physical acceptance requires Instagram, TikTok, and YouTube installed on a connected iPhone. For each host, measure from extension presentation to completion, including provider loading, and verify less than 400 ms. Repeat while the main app is backgrounded and stopped. Disable networking, share, reconnect, and observe processing without touching the queue. Repeat 50 times while checking process memory and crash reports. Capture network activity by extension PID; a static source scan alone does not prove a measured zero-call result.

The native writer logs `queue_write_ms` and `completion_ms` through the system log. Main-app background refresh is scheduled by iOS, and force-quitting the main app can prevent background processing until it is opened again. Queue persistence and immediate background processing are separate checks.

Actual measurements and unavailable cases are recorded in `VERIFICATION.md`. The paired physical devices were unavailable during this run, and the simulator has no installed copies of the three host apps; their device-level checks must therefore be reported as failures until executed.
