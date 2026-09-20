//! The boot-view clock, and nothing else.
//!
//! Every `*_ms` field in this crate's telemetry is measured against one origin, and this is it: a
//! single `Instant` anchored the first time boot-view code asks for the time.
//! `counters::LOG_EPOCH_OFFSET_MS` is the measured gap between this clock and the log's own epoch,
//! and the comment above it has described this module from the outside since 2026-08-22.
//!
//! It lives here rather than in the product's `gpu_readback::boot_progress` because the clock
//! outlived the thing it was named after -- the System>Quit switch guards, the input block, the
//! own-stepper and the native loading-screen exposure all stamp against it, and none of them draws
//! a cover. Leaving the clock inside the cover module made `loading-cover` a feature seven
//! unrelated callers depended on. The cover itself -- the bar, the portrait, the stat block, the
//! compositor -- stays in the product behind that feature.
//!
//! The extraction was done once before, in the `er-quit-rows` fork, which was the one build where
//! `loading-cover` gated anything at all (17 sites, against 0 in the product). Deleting that fork
//! on 2026-09-20 would have deleted the extraction with it, so it lands here rather than back in
//! the crate whose feature set is the problem.
//!
//! Pure `std`: no game memory, no hook and no counter of its own, so it compiles on the host as
//! well as on the shipping target.

/// The origin. Anchored on the first [`boot_view_epoch_ms`] call and never re-anchored.
static BOOT_VIEW_EPOCH: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();

/// Milliseconds since the boot-view epoch, anchoring it on the first call.
pub fn boot_view_epoch_ms() -> u64 {
    let epoch = *BOOT_VIEW_EPOCH.get_or_init(std::time::Instant::now);
    epoch.elapsed().as_millis().min(u64::MAX as u128) as u64
}

/// The same clock as [`boot_view_epoch_ms`], read without starting it: `None` until boot-view code
/// has anchored the epoch.
///
/// The distinction is not pedantry. `boot_view_epoch_ms` anchors on first call, so a caller outside
/// the boot view that happens to run first would silently move the origin of the clock every
/// telemetry `*_ms` field is measured against -- rewriting the meaning of the whole run's timeline
/// to stamp one event. Callers that only want to read the timeline (the in-game menu open stamp,
/// the clock map) use this and simply decline to stamp while the clock does not exist yet.
pub fn boot_view_epoch_ms_if_anchored() -> Option<u64> {
    BOOT_VIEW_EPOCH
        .get()
        .map(|epoch| epoch.elapsed().as_millis().min(u64::MAX as u128) as u64)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Reading without anchoring leaves the clock unstarted, and the anchoring reader starts it.
    ///
    /// One test rather than two, deliberately: the epoch is process-wide and never re-anchored, so
    /// a separate test that anchored it would decide by run order whether the other one's
    /// unanchored assertion means anything. Both halves belong in one body, in this order.
    #[test]
    fn the_unanchored_read_does_not_start_the_clock() {
        assert_eq!(boot_view_epoch_ms_if_anchored(), None);
        let _ = boot_view_epoch_ms();
        assert!(boot_view_epoch_ms_if_anchored().is_some());
    }
}
