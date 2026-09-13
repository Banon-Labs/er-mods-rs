//! Why the missing-save picker is on screen, as a value rather than a log line.
//!
//! # The picker used to arrive without a reason anyone could read
//!
//! Eight code paths arm it, and each passed a free-text `reason` that reached exactly one
//! destination: `er-quickload-autoload-debug.log`. Nothing on screen said why the browser had
//! replaced the title, nothing in `er-quickload-telemetry.json` recorded which of the eight had
//! fired, and no test could assert that a run armed the picker for the reason that run was
//! supposed to produce. A user saw a file browser; a probe saw `oracle_save_picker_overlay_armed
//! = 1`. Both are "the picker is up", neither is "and here is what went wrong".
//!
//! This module makes the reason a `MissingSaveReason`: one enum, carried from the arming site
//! into the gate, exported as a telemetry oracle, and rendered as the banner the picker shows
//! over its own listing. The log tags are the same strings as before, so existing greps and the
//! `bd` memories that quote them still find their lines.
//!
//! # Whose fault the reason says it is
//!
//! [`MissingSaveFault`] splits the eight into the two groups that want different words on
//! screen. `Save` means there is positive evidence about the container itself -- it is absent,
//! unreadable, or its slots hold no character -- and the picker is then the correct and complete
//! fix: choose another file. `Loader` means the container looked loadable and the load did not
//! happen anyway, which is this mod failing rather than the save being bad; the picker is still
//! offered, because a different save may well work, but the banner must not tell the user their
//! save is broken when what we measured is that our own load never finished.
//!
//! [`MissingSaveReason::Unrecorded`] is the defect case: the picker is up and no arming site
//! claimed it. It counts separately (`MISSING_SAVE_PICKER_UNRECORDED_ARMS`) so a run that
//! surfaces the picker out of nowhere is a number, not a silence.

use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::Ordering;

use er_telemetry_core::counters::{
    MISSING_SAVE_PICKER_ARM_COUNT, MISSING_SAVE_PICKER_ARM_REASON,
    MISSING_SAVE_PICKER_UNRECORDED_ARMS,
};

use crate::model::PickerStatusMessage;

/// What made the missing-save picker the boot's remaining path forward.
///
/// Discriminants are explicit and stable: they are exported as the `oracle_missing_save_reason`
/// telemetry field, so a probe compares against a number that must not drift when a variant is
/// added. 0 is reserved for "no arming site recorded one".
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MissingSaveReason {
    /// The picker is up and nothing claimed it. A defect in the arming path, not a state a save
    /// can produce.
    Unrecorded = 0,
    /// `DllMain`'s save check found no source at all: a configured `save_file` was rejected, or
    /// no readable default container exists for the active Steam id.
    BootNoUsableSave = 1,
    /// The same answer, reached on the first game-task tick after the Seamless module latch
    /// settled and the container names were finally knowable.
    SettledNoUsableSave = 2,
    /// A save the redirect had already accepted failed to open when the game asked for it.
    RedirectedSaveOpenFailed = 3,
    /// The Continue slot's `CS::ProfileSummary` record read empty-like for an unbroken run of
    /// ticks, with the container on disk agreeing that the slot is vacant.
    ContinueSlotEmpty = 4,
    /// No player, no `InGameStep` request, no live `MenuJob` and no loading screen, together,
    /// for several consecutive ticks: nothing is queued that could ever produce a character.
    BootNeverStartedTheLoad = 5,
    /// The full read ran and its guard refused the result -- the save was read and no character
    /// came out of it.
    FullReadGuardFailed = 6,
    /// The full read's commit aborted because the owner pointer it needed was null.
    FullReadCommitAborted = 7,
    /// The full read's commit aborted because proceeding would have started a new game over the
    /// slot instead of loading it.
    FullReadWouldStartNewGame = 8,
    /// A save the user picked in this very picker passed every validation it has and still did
    /// not load. The one reason that indicts the loader by construction.
    PickedSaveDidNotLoad = 9,
}

/// Whether the reason is evidence about the save, or evidence about this mod.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MissingSaveFault {
    /// The container is missing, unreadable, or holds no loadable character. Picking another
    /// file is the whole fix.
    Save,
    /// The container looked loadable and the load did not happen. Another save may work, but the
    /// banner must not blame the file.
    Loader,
}

impl MissingSaveReason {
    /// The telemetry/oracle code. Stable across releases; new variants take new numbers.
    pub const fn as_code(self) -> usize {
        self as usize
    }

    /// Recover a reason from its code, treating anything unknown as [`Self::Unrecorded`].
    pub const fn from_code(code: usize) -> Self {
        match code {
            1 => Self::BootNoUsableSave,
            2 => Self::SettledNoUsableSave,
            3 => Self::RedirectedSaveOpenFailed,
            4 => Self::ContinueSlotEmpty,
            5 => Self::BootNeverStartedTheLoad,
            6 => Self::FullReadGuardFailed,
            7 => Self::FullReadCommitAborted,
            8 => Self::FullReadWouldStartNewGame,
            9 => Self::PickedSaveDidNotLoad,
            _ => Self::Unrecorded,
        }
    }

    /// The log tag this reason writes into the debug log.
    ///
    /// These are the exact strings the free-text reasons used before, so a grep or a `bd` memory
    /// quoting one still lands on the same line.
    pub const fn log_tag(self) -> &'static str {
        match self {
            Self::Unrecorded => "unrecorded",
            Self::BootNoUsableSave => "boot-save-override-no-usable-save",
            Self::SettledNoUsableSave => "deferred-save-override-no-readable-default",
            Self::RedirectedSaveOpenFailed => "redirected-save-open-fail",
            Self::ContinueSlotEmpty => "product-continue-empty-profile-exhausted",
            Self::BootNeverStartedTheLoad => "product-continue-dead-boot",
            Self::FullReadGuardFailed => "fullread-guard-fail",
            Self::FullReadCommitAborted => "fullread-commit-abort-owner-null",
            Self::FullReadWouldStartNewGame => "fullread-commit-abort-new-game-flag",
            Self::PickedSaveDidNotLoad => "picked-save-did-not-load",
        }
    }

    /// Whether the reason is evidence about the save or about this mod. See [`MissingSaveFault`].
    pub const fn fault(self) -> MissingSaveFault {
        match self {
            Self::BootNoUsableSave
            | Self::SettledNoUsableSave
            | Self::RedirectedSaveOpenFailed
            | Self::ContinueSlotEmpty
            | Self::FullReadWouldStartNewGame => MissingSaveFault::Save,
            Self::Unrecorded
            | Self::BootNeverStartedTheLoad
            | Self::FullReadGuardFailed
            | Self::FullReadCommitAborted
            | Self::PickedSaveDidNotLoad => MissingSaveFault::Loader,
        }
    }

    /// The banner the picker shows over its listing, naming the picked file where one is known.
    ///
    /// Written for the player, in the second person, and the `Loader` reasons say plainly that
    /// the mod could not load a save that looked fine -- a banner that blamed the file for our
    /// own dead boot would send the user hunting a defect in a container that has none.
    pub fn banner(self) -> PickerStatusMessage {
        match self {
            Self::Unrecorded => PickerStatusMessage::new(
                "CHOOSE A SAVE",
                "This mod could not start a character, and did not record why.",
            ),
            Self::BootNoUsableSave | Self::SettledNoUsableSave => PickerStatusMessage::new(
                "NO SAVE TO LOAD",
                "No readable Elden Ring save was found. Choose one to load.",
            ),
            Self::RedirectedSaveOpenFailed => PickerStatusMessage::new(
                "SAVE COULD NOT BE OPENED",
                "The save was found but could not be opened. Choose another.",
            ),
            Self::ContinueSlotEmpty => PickerStatusMessage::new(
                "THAT SLOT HAS NO CHARACTER",
                "The chosen slot is empty. Choose a save with a character in it.",
            ),
            Self::FullReadWouldStartNewGame => PickerStatusMessage::new(
                "NO CHARACTER IN THAT SAVE",
                "Loading it would have started a new game. Choose another save.",
            ),
            Self::BootNeverStartedTheLoad => PickerStatusMessage::new(
                "THE LOAD NEVER STARTED",
                match reason_detail() {
                    Some(detail) => format!("{detail} Choose a save below."),
                    None => {
                        "No character was loaded and nothing was queued to load one. Choose a save below."
                            .to_owned()
                    }
                },
            ),
            Self::FullReadGuardFailed | Self::FullReadCommitAborted => PickerStatusMessage::new(
                "THE SAVE DID NOT LOAD",
                "This mod read the save and no character came out. Choose another.",
            ),
            Self::PickedSaveDidNotLoad => match picked_save_name() {
                Some(name) => PickerStatusMessage::new(
                    "THAT SAVE DID NOT LOAD",
                    format!("{name} passed every check and still did not load. Choose another."),
                ),
                None => PickerStatusMessage::new(
                    "THAT SAVE DID NOT LOAD",
                    "The save you chose passed every check and still did not load.",
                ),
            },
        }
    }
}

/// The banner to draw for whatever reason is currently recorded.
pub fn missing_save_banner() -> PickerStatusMessage {
    missing_save_reason().banner()
}

/// The banner for a pick this run cannot honour, because the redirect is already committed to
/// another save and its pointers are write-once.
///
/// It lives here rather than at the call site so the renderability test below covers it: a
/// message the 5x7 font cannot draw reaches the user as blanks, which is worse than no message.
pub fn redirect_already_committed_banner() -> PickerStatusMessage {
    PickerStatusMessage::new(
        "SAVE CANNOT BE SWAPPED NOW",
        "This run is already committed to another save. Restart to load this one.",
    )
}

/// What the arming site measured, in the words of the thing it measured, so the banner can name
/// the save and the slot instead of describing the class of failure.
static REASON_DETAIL: Mutex<Option<String>> = Mutex::new(None);

fn reason_detail_lock() -> std::sync::MutexGuard<'static, Option<String>> {
    REASON_DETAIL
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Record the specifics behind the reason: the container, the slot, how long it was given. Set it
/// before [`record_missing_save_reason`], for the same ordering reason.
pub fn record_reason_detail(detail: impl Into<String>) {
    *reason_detail_lock() = Some(detail.into());
}

/// The specifics behind the current reason, when the arming site recorded any.
pub fn reason_detail() -> Option<String> {
    reason_detail_lock().clone()
}

/// The save the picker most recently accepted, so a later failure can name it on screen.
static PICKED_SAVE: Mutex<Option<PathBuf>> = Mutex::new(None);

fn picked_save_lock() -> std::sync::MutexGuard<'static, Option<PathBuf>> {
    PICKED_SAVE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// Remember the container a pick just committed to. Called on a completed selection only.
pub fn record_picked_save(path: &Path) {
    *picked_save_lock() = Some(path.to_path_buf());
}

/// The file name of the accepted pick, if there has been one this session.
pub fn picked_save_name() -> Option<String> {
    picked_save_lock().as_ref().and_then(|path| {
        path.file_name()
            .map(|name| name.to_string_lossy().into_owned())
    })
}

/// The full path of the accepted pick, if there has been one this session.
pub fn picked_save_path() -> Option<PathBuf> {
    picked_save_lock().clone()
}

/// Record why the picker is being armed. Call immediately before the gate opens, never after:
/// the overlay reads the reason the instant the gate flips, and a banner seeded from a reason
/// that has not been stored yet is the blank banner this module exists to remove.
pub fn record_missing_save_reason(reason: MissingSaveReason) {
    MISSING_SAVE_PICKER_ARM_REASON.store(reason.as_code(), Ordering::SeqCst);
    MISSING_SAVE_PICKER_ARM_COUNT.fetch_add(1, Ordering::SeqCst);
    if reason == MissingSaveReason::Unrecorded {
        MISSING_SAVE_PICKER_UNRECORDED_ARMS.fetch_add(1, Ordering::SeqCst);
    }
}

/// The reason the picker is currently up, or [`MissingSaveReason::Unrecorded`] if nobody said.
pub fn missing_save_reason() -> MissingSaveReason {
    MissingSaveReason::from_code(MISSING_SAVE_PICKER_ARM_REASON.load(Ordering::SeqCst))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_code_round_trips() {
        for reason in [
            MissingSaveReason::Unrecorded,
            MissingSaveReason::BootNoUsableSave,
            MissingSaveReason::SettledNoUsableSave,
            MissingSaveReason::RedirectedSaveOpenFailed,
            MissingSaveReason::ContinueSlotEmpty,
            MissingSaveReason::BootNeverStartedTheLoad,
            MissingSaveReason::FullReadGuardFailed,
            MissingSaveReason::FullReadCommitAborted,
            MissingSaveReason::FullReadWouldStartNewGame,
            MissingSaveReason::PickedSaveDidNotLoad,
        ] {
            assert_eq!(MissingSaveReason::from_code(reason.as_code()), reason);
        }
    }

    #[test]
    fn an_unknown_code_is_not_silently_a_real_reason() {
        assert_eq!(
            MissingSaveReason::from_code(4096),
            MissingSaveReason::Unrecorded
        );
    }

    #[test]
    fn every_reason_has_banner_copy() {
        for code in 0..=9 {
            let banner = MissingSaveReason::from_code(code).banner();
            assert!(!banner.headline().is_empty(), "code {code} has no headline");
            assert!(!banner.detail().is_empty(), "code {code} has no detail");
        }
    }

    #[test]
    fn a_dead_boot_does_not_blame_the_save() {
        // The four liveness facts read false together because nothing was queued, which is this
        // mod's failure. A banner that told the user their save was bad would be wrong.
        assert_eq!(
            MissingSaveReason::BootNeverStartedTheLoad.fault(),
            MissingSaveFault::Loader
        );
        assert!(
            !MissingSaveReason::BootNeverStartedTheLoad
                .banner()
                .detail()
                .contains("Choose a save with a character")
        );
    }

    #[test]
    fn an_absent_container_is_the_saves_fault() {
        assert_eq!(
            MissingSaveReason::BootNoUsableSave.fault(),
            MissingSaveFault::Save
        );
    }

    /// Every user-facing picker string must be drawable by the overlay's 5x7 font.
    ///
    /// The font was uppercase-only until 2026-09-12 and its unknown-glyph arm is a blank bitmap,
    /// so a sentence written in ordinary prose reached the screen with its lowercase deleted --
    /// measured, from the screen: "This mod never issued the load. Choose a save to try again."
    /// rendered as `T         . C       .`. Nothing checked, because `is_supported_text` existed
    /// and had no callers. This is that caller.
    #[test]
    fn every_user_facing_string_is_drawable_by_the_overlay_font() {
        record_reason_detail("ER0000.co2 slot 2 gave up after 138 ticks");
        let mut messages: Vec<PickerStatusMessage> = (0..=9)
            .map(|code| MissingSaveReason::from_code(code).banner())
            .collect();
        messages.push(redirect_already_committed_banner());
        for rejection in [
            crate::model::PickRejection::NotAFile,
            crate::model::PickRejection::WrongExtension,
            crate::model::PickRejection::Unreadable,
            crate::model::PickRejection::NotBnd4,
            crate::model::PickRejection::NoLoadableCharacter,
            crate::model::PickRejection::PathNotUtf8,
            crate::model::PickRejection::ParentMissing,
        ] {
            messages.push(rejection.status_message("sl2"));
        }
        for change in [
            crate::model::DirectoryChangeError::Empty,
            crate::model::DirectoryChangeError::NotAbsolute,
            crate::model::DirectoryChangeError::NotDirectory,
        ] {
            messages.push(change.status_message());
        }
        for message in messages {
            for text in [message.headline(), message.detail()] {
                let missing: Vec<char> = text
                    .chars()
                    .filter(|c| !er_loading_bar_core::is_supported_text(&c.to_string()))
                    .collect();
                assert!(
                    missing.is_empty(),
                    "the overlay font cannot draw {missing:?} in {text:?}"
                );
            }
        }
    }

    #[test]
    fn a_failed_pick_names_the_file_it_failed_on() {
        record_picked_save(Path::new("/saves/slot-two/ER0000.co2"));
        let banner = MissingSaveReason::PickedSaveDidNotLoad.banner();
        assert!(
            banner.detail().contains("ER0000.co2"),
            "the banner must name the file: {}",
            banner.detail()
        );
    }
}
