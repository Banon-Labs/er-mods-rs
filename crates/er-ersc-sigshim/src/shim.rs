//! Orchestration: resolve `.text`, run each fixup, and write down what happened.
//!
//! Every exit that does not install something says why. This runs before ersc's own init, and a
//! silent no-op here comes back as ersc's fatal-error box with no explanation attached.

use std::path::PathBuf;
use std::sync::OnceLock;

use er_game_base::{log, mem};

use crate::cave::CaveAllocator;
use crate::fixups::{self, Outcome};

/// Install log, written beside the game executable.
const LOG_FILE_NAME: &str = "er-ersc-sigshim.log";

/// Resolved once: the install log's path, or `None` when the game directory is unavailable (in
/// which case the shim still runs, silently).
static LOG_PATH: OnceLock<Option<PathBuf>> = OnceLock::new();

fn log_line(args: std::fmt::Arguments<'_>) {
    let path =
        LOG_PATH.get_or_init(|| log::game_directory_path().map(|dir| dir.join(LOG_FILE_NAME)));
    if let Some(path) = path.as_ref() {
        log::append_line(path, args);
    }
}

fn report(outcome: &Outcome) {
    match outcome {
        Outcome::NotNeeded(what) => log_line(format_args!("SKIP {what}")),
        Outcome::Installed(what) => log_line(format_args!("INSTALLED {what}")),
        Outcome::Refused(why) => log_line(format_args!("REFUSED {why}")),
    }
}

/// Run every fixup, in a fixed order so the log reads the same way twice.
pub(crate) fn install() {
    let Some((text_start, text_len)) = mem::module_text_range() else {
        log_line(format_args!(
            "REFUSED could not resolve the game image's .text; nothing was written"
        ));
        return;
    };
    let mut caves = CaveAllocator::new(text_start, text_len);
    report(&fixups::allocator_locator(text_start, text_len, &mut caves));
    report(&fixups::scadutree_getter(text_start, text_len, &mut caves));
}
