//! The in-game link field behind the System>Quit **Load Build from URL** row.
//!
//! Pressing the row opens the game's own `CS::SoftwareKeyboard` over the Quit dialog, pre-filled
//! with the link on the clipboard (or the bare `?b=` prefix). Accepting a link that validates
//! imports it; accepting one that does not RE-OPENS the field with the text still in it and the
//! reason on the row's help line; backing out applies nothing.
//!
//! # Why "refuse to accept" is a re-open and not a veto
//!
//! There is no native validation hook to say no with. `FUN_14081d3d0`, the job's per-frame result
//! gate, reports `Continue` while `CSMenuManImp+0x858` says the keyboard is up and then reads the
//! controller's result code at `+0x78` -- 2 means the player pressed accept, anything else means
//! they backed out. The validator struct the job is built with (`FUN_140e70920`) holds a max length
//! and flags, not a predicate: nothing in it can refuse a string.
//!
//! So the accept is allowed to complete natively, and the refusal is expressed the only way the
//! engine offers -- by submitting another keyboard job through the same `MenuJobQueue` the row used
//! the first time. The native job owns its own lifetime; we own the decision to ask again. From the
//! player's side the effect is what was asked for: pressing accept on a bad link does not take, the
//! text is still there to fix, and the row says why.
//!
//! # Why the re-open is bounded
//!
//! [`MAX_REJECTED_REOPENS`] caps the chain. The back action always ends it, so the cap is not what
//! lets a player out -- it is what stops a submit that fails for a reason validation cannot see
//! (a queue that never becomes ready, a recipe that stops resolving) from re-arming forever. The
//! save picker learned this the expensive way: an OS dialog that reopened ~57 ms after every cancel,
//! with no way out of the flow (bd `er-effects-rs-rsxi`).

use super::*;

/// How many times one row press may re-open the field after a rejected link before the flow gives
/// up and reports it. Generous for typing, finite against a submit that cannot succeed.
pub(crate) const MAX_REJECTED_REOPENS: usize = 8;

/// Where the editor is. One press drives this from `Idle` and back.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[repr(usize)]
enum EditorPhase {
    /// No field, no pending press.
    Idle = 0,
    /// A press has asked for the field; the menu pump has not submitted it yet.
    Pending = 1,
    /// The native keyboard is up.
    Open = 2,
}

static PHASE: AtomicUsize = AtomicUsize::new(EditorPhase::Idle as usize);

/// The System>Quit dialog the press came from -- the queue the keyboard is submitted on.
static DIALOG: AtomicUsize = AtomicUsize::new(0);

/// How many times this press has re-opened after a rejection.
static REOPENS: AtomicUsize = AtomicUsize::new(0);

/// The text the next submit should open with. Carries the player's rejected input back into the
/// re-opened field, so a typo is corrected rather than retyped.
static NEXT_TEXT: Mutex<Option<String>> = Mutex::new(None);

fn phase() -> EditorPhase {
    match PHASE.load(Ordering::SeqCst) {
        1 => EditorPhase::Pending,
        2 => EditorPhase::Open,
        _ => EditorPhase::Idle,
    }
}

fn set_phase(next: EditorPhase) {
    PHASE.store(next as usize, Ordering::SeqCst);
}

/// Is a link field open or queued? The row press checks this so a second press cannot stack a
/// second field on the first.
pub(crate) fn build_url_editor_active() -> bool {
    phase() != EditorPhase::Idle || build_url_keyboard_active()
}

/// Ask for the link field. Called from the row press, on whatever thread dispatched the activation;
/// it only latches, because building and submitting a `MenuJob` is menu-pump work.
pub(crate) fn request_build_url_editor(dialog: usize) -> bool {
    if dialog == 0 || dialog == TITLE_OWNER_SCAN_START_ADDRESS {
        return false;
    }
    if build_url_editor_active() {
        append_autoload_debug(format_args!(
            "system-quit-build-url: editor already active; ignoring the repeat press"
        ));
        return false;
    }
    // Read the clipboard HERE rather than at submit time: this is the instant the player pressed
    // the row, so it is their clipboard as it was when they asked, not as it is some frames later.
    let initial = build_url_initial_text();
    *NEXT_TEXT
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(initial);
    REOPENS.store(0, Ordering::SeqCst);
    DIALOG.store(dialog, Ordering::SeqCst);
    set_phase(EditorPhase::Pending);
    SYSTEM_QUIT_LOAD_BUILD_URL_EDITOR_OPEN_COUNT.fetch_add(1, Ordering::SeqCst);
    append_autoload_debug(format_args!(
        "system-quit-build-url: link field requested on dialog=0x{dialog:x}"
    ));
    true
}

/// Drop every pointer this editor holds. Called when the Quit dialog is rebuilt or closes, so a
/// later press cannot submit against a dead dialog.
pub(crate) fn reset_build_url_editor_state() {
    set_phase(EditorPhase::Idle);
    DIALOG.store(0, Ordering::SeqCst);
    REOPENS.store(0, Ordering::SeqCst);
    *NEXT_TEXT
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = None;
    reset_build_url_keyboard_state();
}

/// Menu-pump step: submit a queued field, and consume the answer to one that closed.
///
/// Runs from `system_quit_menu_window_run_post`, which IS the game's own `MenuWindowJob::Run`
/// post-hook -- the same context the save picker's editor and the return-title chain submit from.
/// Building or submitting a `MenuJob` from the game task instead produced the Scaleform race that
/// caused the non-deterministic execute faults (bd `system-quit-return-title-scaleform-race`).
///
/// # Safety
///
/// Menu-pump context only.
pub(crate) unsafe fn build_url_editor_menu_pump() {
    // 1. Consume a finished field first, so an accept that re-opens is submitted in this same pass
    //    rather than a frame later.
    if let Some(outcome) = take_build_url_keyboard_outcome() {
        set_phase(EditorPhase::Idle);
        match outcome {
            BuildUrlKeyboardOutcome::Accepted(text) => unsafe { on_accepted(text) },
            BuildUrlKeyboardOutcome::Cancelled => {
                SYSTEM_QUIT_LOAD_BUILD_URL_CANCELLED_COUNT.fetch_add(1, Ordering::SeqCst);
                set_build_url_row_help(er_build_import::BUILD_URL_ROW_HELP);
                append_autoload_debug(format_args!(
                    "system-quit-build-url: backed out of the link field; nothing applied"
                ));
                reset_build_url_editor_state();
            }
            BuildUrlKeyboardOutcome::TextUnreadable => {
                SYSTEM_QUIT_LOAD_BUILD_URL_REFUSED_COUNT.fetch_add(1, Ordering::SeqCst);
                set_build_url_row_help("The link could not be read back from the field");
                append_autoload_debug(format_args!(
                    "system-quit-build-url: the accepted text was unreadable; nothing applied"
                ));
                reset_build_url_editor_state();
            }
        }
    }

    // 2. Submit a queued field.
    if phase() != EditorPhase::Pending {
        return;
    }
    let dialog = DIALOG.load(Ordering::SeqCst);
    if dialog == 0 {
        set_phase(EditorPhase::Idle);
        return;
    }
    let initial: Vec<u16> = {
        let guard = NEXT_TEXT
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let text = guard
            .as_deref()
            .unwrap_or(er_build_import::BUILD_URL_PREFIX);
        text.encode_utf16().chain(core::iter::once(0)).collect()
    };
    // Safety: menu-pump context, live Quit dialog.
    if unsafe { submit_build_url_keyboard(dialog, &initial) } {
        set_phase(EditorPhase::Open);
    }
    // A submit that did not take leaves the phase Pending, so the next pump retries. The usual
    // cause is the dialog's queue still owning the previous job, which clears itself.
}

/// The player pressed accept. Validate, and either import or ask again.
///
/// # Safety
///
/// Menu-pump context.
unsafe fn on_accepted(text: String) {
    match er_build_import::validate_build_url(&text) {
        Ok(share_id) => {
            SYSTEM_QUIT_LOAD_BUILD_URL_ACCEPTED_COUNT.fetch_add(1, Ordering::SeqCst);
            let url = text.trim().to_owned();
            let press = system_quit_start_build_import_url(&url);
            append_autoload_debug(format_args!(
                "system-quit-build-url: ACCEPTED build {share_id} from the link field -> {press:?}"
            ));
            set_build_url_row_help("Importing the build you entered...");
            // Remember it for next time, and for a boot import, but only once it has proven itself
            // by validating -- the config is a file the player also edits by hand.
            persist_build_url(&url);
            reset_build_url_editor_state();
        }
        Err(rejection) => {
            SYSTEM_QUIT_LOAD_BUILD_URL_REJECTED_COUNT.fetch_add(1, Ordering::SeqCst);
            SYSTEM_QUIT_LOAD_BUILD_URL_LAST_REJECTION.store(rejection.code(), Ordering::SeqCst);
            set_build_url_row_help(rejection.indicator());
            let reopens = REOPENS.fetch_add(1, Ordering::SeqCst) + 1;
            if reopens > MAX_REJECTED_REOPENS {
                append_autoload_debug(format_args!(
                    "system-quit-build-url: REFUSED {text:?} ({}) and gave up after {reopens} re-opens; press the row again to retry",
                    rejection.indicator()
                ));
                reset_build_url_editor_state();
                return;
            }
            // Carry the rejected text back into the field so it can be corrected in place. This IS
            // the "did not accept" the player sees: the field they just accepted is in front of
            // them again, unchanged, with the reason on the row behind it.
            *NEXT_TEXT
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(text.clone());
            set_phase(EditorPhase::Pending);
            append_autoload_debug(format_args!(
                "system-quit-build-url: REFUSED {text:?} ({}); re-opening the field ({reopens}/{MAX_REJECTED_REOPENS})",
                rejection.indicator()
            ));
        }
    }
}
