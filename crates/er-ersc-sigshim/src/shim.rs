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

pub(crate) fn log_line(args: std::fmt::Arguments<'_>) {
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

/// Refuse unless the installed Seamless Co-op is the build these fixups were measured against.
///
/// The fixups are not additive. `scadutree_getter` rewrites the entry of the game's
/// `GetScadutreeBlessing` to the bytes ONE ersc build searches for, and those bytes carry
/// 1.16.2's field offset, not the running game's. That is a favour to a specific scanner and
/// damage to anything else, so the scanner has to be shown to be there first.
///
/// Returns `true` when the fixups may run.
fn ersc_build_is_the_measured_one() -> bool {
    match crate::ersc_build::installed_version() {
        Ok(version) if version == crate::ersc_build::MEASURED_VERSION => {
            log_line(format_args!(
                "ersc build: Seamless Co-op v{version}, the build these fixups were measured \
                 against; proceeding"
            ));
            true
        }
        Ok(version) => {
            log_line(format_args!(
                "REFUSED installed Seamless Co-op is v{version}, but every fixup here was \
                 measured against v{}. The AOB shapes this shim rebuilds were transcribed from \
                 v{}'s own fatal-error box, and re-shaping GetScadutreeBlessing's entry for a \
                 scanner that is not present would silently corrupt a co-op session's blessing \
                 level. Nothing was written. To re-arm: measure what v{version} scans for and \
                 update MEASURED_VERSION plus the shapes in fixups.rs together.",
                crate::ersc_build::MEASURED_VERSION,
                crate::ersc_build::MEASURED_VERSION,
            ));
            false
        }
        Err(unknown) => {
            log_line(format_args!(
                "REFUSED {unknown}. Nothing was written: these fixups only make sense as a favour \
                 to a Seamless Co-op build that has been identified."
            ));
            false
        }
    }
}

/// Run every fixup, in a fixed order so the log reads the same way twice.
pub(crate) fn install() {
    if !ersc_build_is_the_measured_one() {
        return;
    }
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
