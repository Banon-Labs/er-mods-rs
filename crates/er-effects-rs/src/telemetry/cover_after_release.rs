// POST-RELEASE COVER WATCH (user report 2026-08-22: "pressing Escape quickly after the loading
// screen fades out -- while the location banner is still on screen -- makes the loading screen and
// portrait briefly reappear and tear down"; timing-dependent on opening the menu swiftly).
//
// WHY THIS EXISTS AT ALL. The run that reproduced the defect, br-20260822-184123-fa3d, left ZERO
// trace in the DLL log. Not a weak trace -- none. Every aggregate counter said the cover was long
// dead (`BOOT_VIEW_STOPPED` latched and never cleared, `oracle_boot_view_epoch_seq=0`,
// `oracle_boot_view_fps_bail_resumes=0`), and the only PER-FRAME oracle we own,
// [`native_ls_exposure_record`], switches ITSELF off in this exact window: it returns unless the
// game's `CS::LoadingScreen` ticked within 250 ms, and in that run the native screen stopped
// ticking ~2.2 s BEFORE our cover stopped. The one moment worth watching was the one moment
// nothing watched. This is the mirror of that function for the other side of the release.
//
// WHAT IT MEASURES, and what each answer means:
//
//   oracle_cover_after_release_samples == 0
//       the watch never opened. Every zero below is then meaningless -- read this FIRST. It is 0
//       when no cover window has stopped yet, or when every stop is older than the watch span.
//   oracle_cover_plate_visible_after_release > 0
//       the GAME's own `CSFakeLoadingScreenImp` cover plate read VISIBLE on a frame that reached
//       Present, after our cover had already released. That is the surface the user described, and
//       it is the game's, not ours. `_max_run` separates a brief reappearance (short run) from a
//       plate that simply never went down (one run as long as the watch).
//   oracle_native_ls_activity_after_release_updates / _fadeouts > 0
//       the game's own loading screen kept working past our release: `CS::LoadingScreen::Update`
//       ticks and Scaleform fade-out stamps, measured as deltas against baselines snapshotted at
//       the stop. A legitimate NEW load starting inside the watch span (a death, a fast travel)
//       would also show here, so read these next to `oracle_portrait_loadwin_history` rather than
//       alone.
//
// This changes NOTHING that is drawn. It is telemetry, and it deliberately does not attempt a
// pixel probe: whether the plate is up is answerable from RAM, and a full-frame readback in the
// Present path is not something to add before the cheap question has been asked.
//
// COST. Two `ReadProcessMemory` self-reads per sampled frame (the fault-guarded singleton walk in
// `fake_loading_screen_visible`) and a handful of relaxed atomics. Bounded by
// `COVER_AFTER_RELEASE_WATCH_MS` so gameplay does not pay for it forever: after a stop the watch
// runs for that span and then goes quiet until the next cover window stops.

/// How long past a cover stop the watch samples. The reported defect happens while the location
/// banner is still up -- seconds, not minutes -- so this is generous by an order of magnitude and
/// still bounds the per-frame cost to one span per cover window. It is a COST bound, not a
/// judgement about when a spurious cover may occur.
const COVER_AFTER_RELEASE_WATCH_MS: u64 = 30_000;

/// Sample the post-release state for one presented frame. Called from
/// [`native_ls_exposure_record`] on exactly the frames that function declines to judge -- the ones
/// where the native loading screen is stale -- so the two never double-count and the existing
/// exposure accounting is untouched.
///
/// Runs on the game render thread inside Present: no allocation, no scan, no lock.
pub(crate) fn cover_after_release_record(base: usize, now_ms: u64) {
    use er_telemetry::counters::{
        BOOT_VIEW_STOP_LS_FADEOUT_BASELINE, BOOT_VIEW_STOP_LS_UPDATE_BASELINE, BOOT_VIEW_STOP_MS,
        BOOT_VIEW_STOPPED, COVER_PLATE_AFTER_RELEASE_SAMPLES, COVER_PLATE_VISIBLE_AFTER_RELEASE,
        COVER_PLATE_VISIBLE_AFTER_RELEASE_CUR_RUN, COVER_PLATE_VISIBLE_AFTER_RELEASE_FIRST_MS,
        COVER_PLATE_VISIBLE_AFTER_RELEASE_LAST_MS, COVER_PLATE_VISIBLE_AFTER_RELEASE_MAX_RUN,
        LOADING_SCREEN_GFX_FADEOUT_HITS, LOADING_SCREEN_UPDATE_HITS,
        NATIVE_LS_ACTIVITY_AFTER_RELEASE_FADEOUTS, NATIVE_LS_ACTIVITY_AFTER_RELEASE_FIRST_MS,
        NATIVE_LS_ACTIVITY_AFTER_RELEASE_UPDATES,
    };
    // `BOOT_VIEW_STOPPED`, not `BOOT_VIEW_FADE_COMPLETE_MS`: the FPS-bail exit never sets the
    // latter, so keying on it would leave a bail-stopped window unwatched.
    if BOOT_VIEW_STOPPED.load(Ordering::SeqCst) == 0 {
        return;
    }
    let stop_ms = BOOT_VIEW_STOP_MS.load(Ordering::SeqCst) as u64;
    if stop_ms == 0 || now_ms.saturating_sub(stop_ms) > COVER_AFTER_RELEASE_WATCH_MS {
        return;
    }
    COVER_PLATE_AFTER_RELEASE_SAMPLES.fetch_add(1, Ordering::SeqCst);
    let now_usize = now_ms as usize;
    // The game's own loading screen, measured as a delta so a counter that is cumulative for the
    // whole process still answers "did it do anything AFTER we let go".
    let updates = LOADING_SCREEN_UPDATE_HITS
        .load(Ordering::SeqCst)
        .saturating_sub(BOOT_VIEW_STOP_LS_UPDATE_BASELINE.load(Ordering::SeqCst));
    let fadeouts = LOADING_SCREEN_GFX_FADEOUT_HITS
        .load(Ordering::SeqCst)
        .saturating_sub(BOOT_VIEW_STOP_LS_FADEOUT_BASELINE.load(Ordering::SeqCst));
    NATIVE_LS_ACTIVITY_AFTER_RELEASE_UPDATES.fetch_max(updates, Ordering::SeqCst);
    NATIVE_LS_ACTIVITY_AFTER_RELEASE_FADEOUTS.fetch_max(fadeouts, Ordering::SeqCst);
    if updates + fadeouts > 0 {
        let _ = NATIVE_LS_ACTIVITY_AFTER_RELEASE_FIRST_MS.compare_exchange(
            0,
            now_usize,
            Ordering::SeqCst,
            Ordering::SeqCst,
        );
    }
    // The decisive read: is the game's render-pipeline cover plate up on THIS presented frame?
    let visible = unsafe { crate::experiments::fake_loading_screen_visible(base) };
    if !visible {
        COVER_PLATE_VISIBLE_AFTER_RELEASE_CUR_RUN.store(0, Ordering::SeqCst);
        return;
    }
    let n = COVER_PLATE_VISIBLE_AFTER_RELEASE.fetch_add(1, Ordering::SeqCst) + 1;
    let run = COVER_PLATE_VISIBLE_AFTER_RELEASE_CUR_RUN.fetch_add(1, Ordering::SeqCst) + 1;
    COVER_PLATE_VISIBLE_AFTER_RELEASE_MAX_RUN.fetch_max(run, Ordering::SeqCst);
    COVER_PLATE_VISIBLE_AFTER_RELEASE_LAST_MS.store(now_usize, Ordering::SeqCst);
    let _ = COVER_PLATE_VISIBLE_AFTER_RELEASE_FIRST_MS.compare_exchange(
        0,
        now_usize,
        Ordering::SeqCst,
        Ordering::SeqCst,
    );
    // First frame of each run only -- a sustained plate is ONE event, and this is the Present hook.
    if run == 1 {
        append_autoload_debug(format_args!(
            "COVER-AFTER-RELEASE #{n}: the game's own CSFakeLoadingScreenImp plate is VISIBLE at {now_usize}ms, {}ms after our cover stopped (stop_reason={} native_ls_updates_since_stop={updates} fadeouts_since_stop={fadeouts}) -- this surface is the GAME's, not our compositor's",
            now_ms.saturating_sub(stop_ms),
            er_telemetry::counters::BOOT_VIEW_STOP_REASON.load(Ordering::SeqCst),
        ));
    }
}

/// Emit the post-release watch oracles plus the boot-view null detector. Called from the telemetry
/// writer.
pub(crate) fn cover_after_release_write(body: &mut String) {
    use er_telemetry::counters::{
        BOOT_VIEW_DRAW_AFTER_STOP, BOOT_VIEW_DRAW_AFTER_STOP_FIRST_MS,
        BOOT_VIEW_DRAW_AFTER_STOP_TOTAL, BOOT_VIEW_STOP_MS, COVER_PLATE_AFTER_RELEASE_SAMPLES,
        COVER_PLATE_VISIBLE_AFTER_RELEASE, COVER_PLATE_VISIBLE_AFTER_RELEASE_FIRST_MS,
        COVER_PLATE_VISIBLE_AFTER_RELEASE_LAST_MS, COVER_PLATE_VISIBLE_AFTER_RELEASE_MAX_RUN,
        NATIVE_LS_ACTIVITY_AFTER_RELEASE_FADEOUTS, NATIVE_LS_ACTIVITY_AFTER_RELEASE_FIRST_MS,
        NATIVE_LS_ACTIVITY_AFTER_RELEASE_UPDATES,
    };
    push_json_usize(
        body,
        "oracle_boot_view_draw_after_stop",
        BOOT_VIEW_DRAW_AFTER_STOP.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_boot_view_draw_after_stop_total",
        BOOT_VIEW_DRAW_AFTER_STOP_TOTAL.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_boot_view_draw_after_stop_first_ms",
        BOOT_VIEW_DRAW_AFTER_STOP_FIRST_MS.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_boot_view_stop_ms",
        BOOT_VIEW_STOP_MS.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_cover_after_release_samples",
        COVER_PLATE_AFTER_RELEASE_SAMPLES.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_cover_plate_visible_after_release",
        COVER_PLATE_VISIBLE_AFTER_RELEASE.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_cover_plate_visible_after_release_first_ms",
        COVER_PLATE_VISIBLE_AFTER_RELEASE_FIRST_MS.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_cover_plate_visible_after_release_last_ms",
        COVER_PLATE_VISIBLE_AFTER_RELEASE_LAST_MS.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_cover_plate_visible_after_release_max_run",
        COVER_PLATE_VISIBLE_AFTER_RELEASE_MAX_RUN.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_native_ls_activity_after_release_updates",
        NATIVE_LS_ACTIVITY_AFTER_RELEASE_UPDATES.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_native_ls_activity_after_release_fadeouts",
        NATIVE_LS_ACTIVITY_AFTER_RELEASE_FADEOUTS.load(Ordering::SeqCst),
    );
    push_json_usize(
        body,
        "oracle_native_ls_activity_after_release_first_ms",
        NATIVE_LS_ACTIVITY_AFTER_RELEASE_FIRST_MS.load(Ordering::SeqCst),
    );
}
