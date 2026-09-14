//! Runtime lifecycle seams for attach-time experiment hook installation.
//!
//! Keep hook ordering here behavior-preserving: these functions are thin orchestration
//! wrappers around code that previously lived inline in `DllMain`.

use super::*;

// The Save Game row's flow moved to `er-quit-menu-core` on 2026-09-12 -- the crate that already
// held the row's label, its router and the destination browser it opens. Re-exported under the same
// names so the product's call sites read unchanged, and so any other host can link the same code.
// The facade that stood here is gone: `er-quit-menu-core` links `er-save-suppress` itself now and
// reads the redirect directory through the host seam, so the wrappers had nothing left to wrap.
pub(crate) use er_quit_menu_core::save_dest_commit_runtime::*;
pub(crate) use er_quit_menu_core::save_flow::*;
pub(crate) use er_quit_menu_core::save_flow_boxes::*;
pub(crate) use er_quit_menu_core::save_game_row::*;

mod task_tick;
pub(crate) use task_tick::*;

mod title_visual_startup;
pub(crate) use title_visual_startup::*;

mod hook_installers;
pub(crate) use hook_installers::*;
