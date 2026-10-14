//! Cel shading for Elden Ring, as one me3 native, with an in-game menu to tune it live.
//!
//! # What it changes
//!
//! Elden Ring is deferred. Every light -- sun, tiled point and spot lights, ambient -- is
//! accumulated into one diffuse light buffer, and `GXLightDeferredAO` (or its screen-space
//! reflection twin) then multiplies ambient occlusion into it. This DLL replaces the pixel shader
//! of five passes (see [`replace::REPLACEMENTS`]): the two AO passes band the light and ink
//! outlines, the albedo pass flattens surface colour, and the two tonemap passes posterize and
//! saturate the finished picture, which is what reaches forward-lit hair and fur and glowing
//! things.
//!
//! # How
//!
//! It hooks `D3D12CreateDevice`, then the device's pipeline-creation entry points, and swaps the
//! pixel shader bytecode in a copy of the description when it matches a replacement (matched by
//! FNV-1 64 of the game's DXIL). Because it replaces what the game hands D3D12, the same DLL works
//! on native Windows and under Proton.
//!
//! The replacement shaders are HLSL in `shaders/`, compiled and signed by
//! `scripts/build-cel-shaders.py`; the DLL embeds the result and needs no compiler at run time.
//! They read the look from a constant buffer this DLL binds ([`look_binding`]), so the menu
//! ([`menu`], default key F6) changes the picture on the next frame and saves it to
//! `er-cel-shade.toml` beside the game ([`settings`]).
//!
//! # Evidence this rests on
//!
//! Measured 2026-10-14 under Proton with vkd3d-proton shader overrides, before this DLL existed:
//! a pass painted solid magenta covered the whole frame, and posterizing these two passes was
//! visible on screen where hardening the sun alone was not.

// The host build compiles only the pure modules; everything that calls into them is Windows-only.
#![cfg_attr(not(windows), allow(dead_code))]

pub mod replace;
pub mod settings;

#[cfg(windows)]
mod hooks;
#[cfg(windows)]
mod input;
#[cfg(windows)]
mod look_binding;
#[cfg(windows)]
mod menu;
#[cfg(windows)]
mod overlay;

#[cfg(windows)]
use std::sync::Once;

/// `DLL_PROCESS_ATTACH`.
#[cfg(windows)]
const DLL_PROCESS_ATTACH: u32 = 1;

/// `DllMain` must return true or the loader unloads us.
#[cfg(windows)]
const DLL_MAIN_SUCCESS: i32 = 1;

/// Log file name, beside the game executable like every other shell in this repo.
#[cfg(windows)]
const LOG_FILE_NAME: &str = "er-cel-shade.log";

#[cfg(windows)]
static START: Once = Once::new();

#[cfg(windows)]
fn log_path() -> std::path::PathBuf {
    er_game_base::log::game_directory_path()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(LOG_FILE_NAME)
}

#[cfg(windows)]
pub(crate) fn log(args: std::fmt::Arguments<'_>) {
    er_game_base::log::append_line(&log_path(), format_args!("er-cel-shade: {args}"));
}

// Every shell that links the overlay host exports its registration, so a guest can find whichever
// module won the overlay.
#[cfg(windows)]
er_build_watermark_core::export_overlay_host!();

#[cfg(windows)]
#[unsafe(no_mangle)]
/// # Safety
///
/// Called by the Windows loader. Do not call directly.
pub unsafe extern "system" fn DllMain(
    module: *mut core::ffi::c_void,
    reason: u32,
    _reserved: *mut core::ffi::c_void,
) -> i32 {
    if reason == DLL_PROCESS_ATTACH {
        // Before anything that can panic: a panic in a hook crosses an `extern "system"` boundary
        // and aborts without a crash record unless this is installed.
        er_game_base::panic_report::report_panics_to("er-cel-shade", log);
        let module_base = module as usize;
        START.call_once(|| {
            er_hook::set_hook_logger(log);
            log(format_args!(
                "loaded module_base=0x{module_base:x} replacements={} overlay ABI {:#06x}",
                replace::REPLACEMENTS.len(),
                er_build_watermark_core::overlay_host::OVERLAY_ABI_TAG
            ));
            // Read before the device exists, so the first frame already has the saved look.
            let (settings, warnings) = menu::load_settings();
            menu::init(settings, &warnings);
            // In `DllMain`, not on a thread: the game creates its device during start-up, and a
            // hook that lands after that sees none of the root signatures or pipelines.
            match hooks::install() {
                Ok(()) => log(format_args!("waiting for D3D12CreateDevice")),
                Err(error) => log(format_args!("not installed: {error}")),
            }
            // The overlay and the cursor hook take locks and enumerate modules, neither of which
            // belongs under the loader lock.
            let _ = std::thread::Builder::new()
                .name("er-cel-shade-install".to_owned())
                .spawn(move || {
                    input::install_cursor();
                    overlay::install(module_base);
                });
        });
    }
    DLL_MAIN_SUCCESS
}
