//! The build watermark: a faint list, top right, of every one of this workspace's DLLs in the
//! process and how each stands against `main`.
//!
//! # Why it exists
//!
//! On 2026-08-24 a tester reported a crash against an invasion-warp DLL three days older than the
//! fix for that exact crash, sitting beside two DLLs built the same afternoon. Nothing on screen
//! said so; the only way to establish it was parsing the module table out of a minidump. This is
//! that answer, permanently on screen, for anyone who screenshots anything.
//!
//! # Loud only when it must be
//!
//! Quiet states are drawn at **1%** -- present in a screenshot, effectively invisible while
//! playing. The single loud state, **25% red**, is a build that is an older published release
//! than `main`'s tip: somebody is running code we have already moved past, and a bug report
//! against it may describe something already fixed. A dirty local tree is not that, and is drawn
//! as quietly as the tip -- see `er_game_base::build_id::Standing`.
//!
//! # Why hudhook rather than the D3D12 compositor
//!
//! `er-d3d12-compositor` copies an RGBA frame onto the backbuffer; it never blends, so "1%
//! opacity" is not expressible through it at all -- the first cut of this watermark had to paint
//! an opaque dark panel and call it faint. Real alpha through that path would have meant either a
//! per-frame backbuffer readback (a fence stall inside `Present`) or a blend pipeline with its
//! own shaders. `er-net-effects` already runs a hudhook/imgui DX12 overlay in this same
//! process, alongside the product's own `Present` hook, and `DrawList::add_text` takes a float
//! alpha imgui blends for free. Reusing it costs one dependency and no stall.

pub mod font_request;
pub mod layout;
pub mod releases;
pub mod texture_request;

#[cfg(windows)]
mod overlay;

/// The system clipboard behind every overlay text field; installed by the host.
#[cfg(windows)]
mod clipboard;

/// One imgui host per process, and a way for every other module to draw on it.
#[cfg(windows)]
pub mod overlay_host;

#[cfg(windows)]
pub use overlay::{
    OverlayClaim, claim_overlay_ownership, claim_owner, draw_rows, install_if_owner, render_hits,
    visible_rows,
};

/// Host stub so callers compile on Linux; there is no swapchain to draw on.
#[cfg(not(windows))]
pub fn install_if_owner(_hmodule_raw: usize, _log: fn(std::fmt::Arguments<'_>)) -> bool {
    false
}

#[cfg(test)]
mod every_host_builds_one_default_font {
    use std::path::{Path, PathBuf};

    fn sources(dir: &Path, out: &mut Vec<PathBuf>) {
        for entry in std::fs::read_dir(dir).into_iter().flatten().flatten() {
            let path = entry.path();
            if path.is_dir() {
                sources(&path, out);
            } else if path.extension().is_some_and(|e| e == "rs") {
                out.push(path);
            }
        }
    }

    /// Whichever module wins the overlay builds the default face every guest falls back to, so
    /// every host must build the same one. Until 2026-10-16 the watermark built 27 pixels,
    /// er-net-effects 16.25 and the rest Dear ImGui's 13, so a guest's text size depended on which
    /// DLL won. Every render loop a crate can host therefore calls
    /// `overlay_host::add_host_default_font` in the file that implements it.
    #[test]
    fn every_render_loop_calls_add_host_default_font() {
        let crates = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("crates/");
        let mut hosts = Vec::new();
        let mut missing = Vec::new();
        for entry in std::fs::read_dir(crates)
            .expect("crates/ is readable")
            .flatten()
        {
            let mut files = Vec::new();
            sources(&entry.path().join("src"), &mut files);
            for file in files {
                let Ok(text) = std::fs::read_to_string(&file) else {
                    continue;
                };
                if !text.contains("impl ImguiRenderLoop for") {
                    continue;
                }
                let name = file.display().to_string();
                if !text.contains("add_host_default_font") {
                    missing.push(name.clone());
                }
                hosts.push(name);
            }
        }
        assert!(
            hosts.len() >= 5,
            "found only {hosts:?}; the scan no longer sees the render loops"
        );
        assert!(
            missing.is_empty(),
            "these render loops can host the overlay but build their own default face, so a \
             guest's fallback text size depends on which DLL won: {missing:?}. Call \
             `er_build_watermark_core::overlay_host::add_host_default_font(ctx)` in `initialize`."
        );
    }
}
