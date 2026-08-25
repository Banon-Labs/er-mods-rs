//! The hudhook/imgui render loop, and the arbitration that keeps exactly one DLL owning it.

use std::ffi::c_void;
use std::sync::atomic::{AtomicUsize, Ordering};

use hudhook::hooks::dx12::ImguiDx12Hooks;
use hudhook::imgui::{Context, FontConfig, FontSource, Ui};
use hudhook::{ImguiRenderLoop, RenderContext};

use crate::layout;

/// Renders survived, for telemetry. A watermark that is installed but never renders and one that
/// renders every frame are the same silence otherwise.
static RENDER_HITS: AtomicUsize = AtomicUsize::new(0);

/// Rows drawn on the most recent render, so a run can tell "the roster was empty" apart from
/// "the draw never happened" without anyone reading a screenshot.
static VISIBLE_ROWS: AtomicUsize = AtomicUsize::new(0);

/// How often the roster and the standings are recomputed, in renders.
///
/// The roster is fixed once the loader finishes, but the standings are not: the release lookup
/// answers on a background thread seconds into the run, and a watermark that never recomputed
/// would stay quiet having learned that something IS behind and not shown it.
const REBUILD_EVERY_RENDERS: usize = 120;

/// Process-wide name of the watermark owner.
///
/// hudhook's own install latch is a plain `static` and statics are PER DLL, so two of our DLLs
/// each calling `Hudhook::apply()` would both believe they were first and double-hook `Present`.
/// A named kernel object is visible across every module in the process, which is exactly the
/// scope the question has. `Local\` keeps it per-session rather than machine-wide.
const OWNER_MUTEX_NAME: windows::core::PCWSTR =
    windows::core::w!("Local\\er-effects-build-watermark-owner");

/// Renders the roster. State is the cached rows plus the counter that ages them out.
struct WatermarkOverlay {
    rows: Vec<layout::WatermarkRow>,
    renders: usize,
    log: fn(std::fmt::Arguments<'_>),
}

impl WatermarkOverlay {
    fn new(log: fn(std::fmt::Arguments<'_>)) -> Self {
        Self {
            rows: Vec::new(),
            renders: 0,
            log,
        }
    }

    /// Recompute the roster and every line's standing.
    fn rebuild(&mut self) {
        let identities = er_game_base::build_id::loaded_mod_identities();
        let published = er_game_base::build_id::published_main_shas();
        self.rows = layout::rows(&identities, &published);
    }
}

impl ImguiRenderLoop for WatermarkOverlay {
    fn initialize<'a>(&'a mut self, ctx: &mut Context, _render: &'a mut dyn RenderContext) {
        // A dedicated font size rather than scaling the default: imgui's 13px face scaled up is
        // blurry, and a watermark that is hard to read at 25% defeats the one state that matters.
        ctx.fonts().add_font(&[FontSource::DefaultFontData {
            config: Some(FontConfig {
                size_pixels: layout::FONT_SIZE_PX,
                ..FontConfig::default()
            }),
        }]);
        self.rebuild();
        (self.log)(format_args!(
            "build-watermark: render loop initialized, {} row(s), font {}px",
            self.rows.len(),
            layout::FONT_SIZE_PX
        ));
    }

    fn render(&mut self, ui: &mut Ui) {
        let hits = RENDER_HITS.fetch_add(1, Ordering::Relaxed) + 1;
        if self.renders.is_multiple_of(REBUILD_EVERY_RENDERS) {
            self.rebuild();
        }
        self.renders = self.renders.wrapping_add(1);
        if self.rows.is_empty() {
            VISIBLE_ROWS.store(0, Ordering::Relaxed);
            return;
        }

        let [screen_width, _] = ui.io().display_size;
        // The foreground draw list, so the watermark sits above the game AND above any imgui
        // window another overlay in this process happens to be drawing.
        let draw_list = ui.get_foreground_draw_list();
        let row_height = layout::FONT_SIZE_PX;
        for (index, row) in self.rows.iter().enumerate() {
            let text_width = ui.calc_text_size(&row.text)[0];
            let pos = layout::row_position(screen_width, index, row_height, text_width);
            draw_list.add_text(pos, row.rgba, &row.text);
        }
        VISIBLE_ROWS.store(self.rows.len(), Ordering::Relaxed);

        if hits == 1 {
            (self.log)(format_args!(
                "build-watermark: first render display_width={screen_width} rows={} loudest_alpha={:.2}",
                self.rows.len(),
                self.rows
                    .iter()
                    .map(|row| row.rgba[3])
                    .fold(0.0_f32, f32::max)
            ));
        }
    }
}

/// Number of renders the watermark has survived.
pub fn render_hits() -> usize {
    RENDER_HITS.load(Ordering::Relaxed)
}

/// Rows drawn on the most recent render.
pub fn visible_rows() -> usize {
    VISIBLE_ROWS.load(Ordering::Relaxed)
}

/// Claim process-wide ownership of the hudhook overlay, returning whether THIS call won.
///
/// Split out from [`install_if_owner`] because a second overlay in this workspace --
/// `er-net-effects` -- installs hudhook for its own bar, and two `Hudhook::apply()` calls in
/// one process double-hook `Present`. Whichever module intends to install hudhook calls this
/// first; the loser draws nothing of its own, and (for the watermark) simply lets the winner's
/// render loop carry the rows via [`draw_rows`].
///
/// The handle is never closed, on purpose: ownership must last as long as the process, and
/// closing it would destroy the mutex once no other opener held it, letting a later-loading DLL
/// believe it was first and install a second hook. Nothing is leaked in the Rust sense -- a Win32
/// `HANDLE` is `Copy` with no `Drop`, so simply not calling `CloseHandle` IS the retention.
pub fn claim_owner() -> bool {
    use windows::Win32::Foundation::{ERROR_ALREADY_EXISTS, GetLastError};
    use windows::Win32::System::Threading::CreateMutexW;

    // SAFETY: a named-mutex creation with a static name; the handle is intentionally never closed.
    let Ok(_handle) = (unsafe { CreateMutexW(None, true, OWNER_MUTEX_NAME) }) else {
        return false;
    };
    // ERROR_ALREADY_EXISTS means the mutex was created by an earlier caller and this call merely
    // opened it -- so somebody else is the owner.
    let last = unsafe { GetLastError() };
    last != ERROR_ALREADY_EXISTS
}

/// Draw the watermark rows onto an EXISTING imgui frame.
///
/// For a module that already owns a hudhook render loop and should carry the watermark inside it
/// rather than have a second overlay installed behind its back. Recomputes the roster on the
/// caller's cadence; cheap enough at the rate a render loop calls it, and always current.
pub fn draw_rows(ui: &Ui, log: fn(std::fmt::Arguments<'_>)) {
    static FIRST: std::sync::Once = std::sync::Once::new();
    let identities = er_game_base::build_id::loaded_mod_identities();
    let published = er_game_base::build_id::published_main_shas();
    let rows = layout::rows(&identities, &published);
    if rows.is_empty() {
        VISIBLE_ROWS.store(0, Ordering::Relaxed);
        return;
    }
    let [screen_width, _] = ui.io().display_size;
    let draw_list = ui.get_foreground_draw_list();
    for (index, row) in rows.iter().enumerate() {
        let text_width = ui.calc_text_size(&row.text)[0];
        let pos = layout::row_position(screen_width, index, layout::FONT_SIZE_PX, text_width);
        draw_list.add_text(pos, row.rgba, &row.text);
    }
    VISIBLE_ROWS.store(rows.len(), Ordering::Relaxed);
    RENDER_HITS.fetch_add(1, Ordering::Relaxed);
    // Once. A shared draw path that says nothing is indistinguishable from one that never ran,
    // and this one lives inside somebody else's render loop where it has no log of its own.
    FIRST.call_once(|| {
        let loudest = rows.iter().map(|row| row.rgba[3]).fold(0.0_f32, f32::max);
        let behind = rows.iter().filter(|row| row.rgba[3] > 0.01).count();
        log(format_args!(
            "build-watermark: first embedded draw -- {} row(s), {behind} behind main, \
             loudest_alpha={loudest:.2}, display_width={}",
            rows.len(),
            screen_width
        ));
    });
}

/// Install a STANDALONE watermark overlay, if no other module in this process owns hudhook.
///
/// Returns whether this call became the owner. A `false` return is the ordinary, correct outcome
/// for every module after the first, and is not an error.
pub fn install_if_owner(hmodule_raw: usize, log: fn(std::fmt::Arguments<'_>)) -> bool {
    if !claim_owner() {
        return false;
    }
    crate::releases::spawn_lookup(log);
    let hmodule = hudhook::windows::Win32::Foundation::HINSTANCE(hmodule_raw as *mut c_void);
    let result = hudhook::Hudhook::builder()
        .with::<ImguiDx12Hooks>(WatermarkOverlay::new(log))
        .with_hmodule(hmodule)
        .build()
        .apply();
    match result {
        Ok(()) => {
            log(format_args!(
                "build-watermark: hudhook dx12 overlay installed (this module owns the watermark)"
            ));
            true
        }
        Err(error) => {
            log(format_args!(
                "build-watermark: hudhook dx12 overlay install FAILED: {error:?}"
            ));
            false
        }
    }
}
