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
//!
//! # Why the clipboard is read while the field is OPEN and not only when it opens
//!
//! Ctrl+V inside the field does nothing -- the field has no paste, and the one native clipboard
//! reader in the image is unreachable from it (`build_url_clipboard`'s module docs carry the
//! evidence). For a while the answer was "read the clipboard once, when the field opens", and the
//! user's own session shows exactly how that fails, from `er-effects-autoload-debug.log`
//! (`dll:41638ed8`, 2026-08-23):
//!
//! ```text
//! [+47142ms]  submitted native SoftwareKeyboardJob  initial_units=43   <- the bare prefix
//!             ... the player pastes, and waits 36 seconds ...
//! [+83008ms]  native editor accepted text="https://er-build-planner.nyasu.business/?b="  43 units
//! [+83009ms]  REFUSED (nothing was typed after the prefix)
//! [+155745ms] prefilling the editor from the clipboard (57 chars)
//! [+155747ms] submitted native SoftwareKeyboardJob  initial_units=57
//! [+158095ms] native editor accepted ...?b=bc2a932db14675  -> ACCEPTED, import applied
//! ```
//!
//! Two things are proven there. The round trip is sound: 36 seconds in the field returned EXACTLY
//! the 43 units that were put in it, so nothing is stale, dangling or misread. And the link only
//! appeared on a LATER open because that is the only moment this DLL looked at the clipboard. The
//! player experiences that as a one-entry lag -- paste, see nothing, accept, back out, re-enter,
//! and there it is.
//!
//! So the mirror runs every frame the field is up: [`clipboard_sequence`] is a lock-free counter
//! that says whether anything was copied at all, and only a change justifies a real read. A read
//! that yields an importable link REPLACES the field through the game's own SetText. An unrelated
//! copy cannot pass [`clipboard_build_url`]'s validation, so it cannot overwrite what the player is
//! typing.
//!
//! The first few frames also re-read unconditionally, because under Wine the X11-to-Win32 clipboard
//! bridge is asynchronous: a link copied moments before the row press can land after it, with the
//! sequence number already bumped before we first looked.

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

/// The last link this DLL put into the live field, so the mirror re-pushes only on a real change.
/// Cleared with the rest of the editor state, because a stale value here would make the first paste
/// of the NEXT open look like a repeat and be skipped.
static MIRRORED_TEXT: Mutex<Option<String>> = Mutex::new(None);

/// The clipboard write-count last acted on. A frame whose [`clipboard_sequence`] still equals this
/// costs one lock-free read and nothing else.
static MIRRORED_SEQUENCE: AtomicUsize = AtomicUsize::new(0);

/// Frames of the open field over which the clipboard is re-read even when its sequence number has
/// not moved.
///
/// Wine's clipboard bridge is asynchronous: a link copied just before the row press can be visible
/// to `GetClipboardSequenceNumber` before its DATA can be fetched, so the open-time read misses it
/// and no later change ever arrives to re-trigger the mirror. A short unconditional window closes
/// that hole. It is short enough that a player cannot type inside it, and the validation gate means
/// even a re-read inside it cannot overwrite anything with non-link text.
const MIRROR_UNCONDITIONAL_FRAMES: usize = 30;

/// How far apart those unconditional re-reads are spaced.
///
/// A real read takes the process-wide clipboard lock, and under Wine that is a round trip to the
/// X11 selection owner. Doing it on every frame of the window would be three dozen of them in half
/// a second, for a bridge whose whole problem is that it is slow. Spacing them gives the same
/// coverage for a handful of reads.
const MIRROR_UNCONDITIONAL_STRIDE: usize = 10;

/// Frames the open field has run. Reset per open.
static MIRROR_FRAMES: AtomicUsize = AtomicUsize::new(0);

/// The 02_990 MenuWindow this editor has been driving.
///
/// Both fields load the SAME movie, so the run post-hook has to decide which editor a given window
/// belongs to, and "is the build-url keyboard up right now" alone is one frame too narrow: the
/// keyboard's job slot clears the instant the terminal callback fires, while its window still runs
/// for a frame or two afterwards. Those frames would be handed to the save picker's editor state,
/// which is exactly what the routing exists to prevent. Remembering the pointer closes that gap.
static EDITOR_WINDOW: AtomicUsize = AtomicUsize::new(0);

/// Mirror outcomes logged this open. A per-frame path that logged every attempt would bury the rest
/// of the trace, and one that logged only the first would hide a success that followed a failure.
static MIRROR_LOGS: AtomicUsize = AtomicUsize::new(0);

/// Cap on those log lines per open.
const MIRROR_LOG_LIMIT: usize = 4;

/// Frames of the open field over which the end-caret is re-applied. Matches the path editor's own
/// window: the field may not be focused on the frame it first runs, and taking focus is what resets
/// a caret set too early. Each application resolves and destroys two SceneObjProxies, so it is kept
/// as short as it can be while still outlasting focus.
const CARET_APPLY_FRAMES: usize = 8;

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
    reset_build_url_mirror();
    reset_build_url_keyboard_state();
}

/// Forget what the mirror has pushed. Called on every open and close: a value carried across opens
/// would make the next open's first paste look like a repeat and be skipped.
fn reset_build_url_mirror() {
    *MIRRORED_TEXT
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner()) = None;
    MIRRORED_SEQUENCE.store(0, Ordering::SeqCst);
    MIRROR_FRAMES.store(0, Ordering::SeqCst);
    MIRROR_LOGS.store(0, Ordering::SeqCst);
    EDITOR_WINDOW.store(0, Ordering::SeqCst);
}

/// Does the link field own this 02_990 MenuWindow?
///
/// The keyboard being up settles it outright. Past that, only a window this editor was already
/// driving counts, and only while the save picker has no editor of its own running -- so a recycled
/// window address can never make the picker's field answer to the link field's state.
pub(crate) fn build_url_editor_owns_window(window: usize) -> bool {
    if build_url_keyboard_active() {
        return true;
    }
    window != 0
        && EDITOR_WINDOW.load(Ordering::SeqCst) == window
        && !save_picker_path_editor_active()
}

/// Should this frame read the clipboard for real, rather than trusting its sequence number?
///
/// `reopens` is how many times this press has already been refused, and it is what keeps the
/// unconditional window off a RE-open. On a first open the field holds whatever the clipboard said
/// at press time, so re-reading can only improve it. On a re-open the field holds text the PLAYER
/// edited into being wrong, and re-reading the same unchanged clipboard would throw their edit away
/// and hand back the link they were in the middle of correcting. Past that, a genuine clipboard
/// CHANGE still lands either way -- copying a new link is a deliberate act.
///
/// Split out so the rule is testable without a live field.
fn mirror_rereads_unconditionally(frame: usize, reopens: usize) -> bool {
    reopens == 0
        && frame < MIRROR_UNCONDITIONAL_FRAMES
        && frame.is_multiple_of(MIRROR_UNCONDITIONAL_STRIDE)
}

/// Per-frame work for the live link field's own `02_990` MenuWindow.
///
/// Called from `system_quit_menu_window_run_post` when the window that just ran is the 02_990 movie
/// AND the build-url keyboard owns it -- the same context the save picker's editor uses for its
/// caret, and the only context in which the field's SceneObjProxies are safe to resolve.
///
/// Two jobs, in order: put the caret at the end of the prefilled link so typing appends, and mirror
/// a newly copied link into the field. Both are no-ops once satisfied.
///
/// # Safety
///
/// 02_990 `MenuWindowJob::Run` context, `menu_window` live.
pub(crate) unsafe fn build_url_editor_window_run(base: usize, menu_window: usize) {
    EDITOR_WINDOW.store(menu_window, Ordering::SeqCst);
    let frame = MIRROR_FRAMES.fetch_add(1, Ordering::SeqCst);
    if frame < CARET_APPLY_FRAMES {
        // The field is not necessarily focused on the frame its window first runs, and taking focus
        // is what resets a caret set too early -- so this repeats over the same short window the
        // path editor's caret pass uses. Without it the link field opens with its caret at index 0
        // and a player's first keystroke lands in FRONT of the prefilled link.
        let _ = unsafe { place_text_input_02_990_caret_at_end(base, menu_window) };
    }
    unsafe { mirror_clipboard_into_field(base, menu_window, frame) };
}

/// Push a newly copied link into the open field, if there is one.
///
/// # Safety
///
/// 02_990 `MenuWindowJob::Run` context, `menu_window` live.
unsafe fn mirror_clipboard_into_field(base: usize, menu_window: usize, frame: usize) {
    let sequence = clipboard_sequence() as usize;
    if sequence == MIRRORED_SEQUENCE.load(Ordering::SeqCst)
        && !mirror_rereads_unconditionally(frame, REOPENS.load(Ordering::SeqCst))
    {
        return;
    }
    MIRRORED_SEQUENCE.store(sequence, Ordering::SeqCst);
    let Some(link) = clipboard_build_url() else {
        return;
    };
    {
        let mut mirrored = MIRRORED_TEXT
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if mirrored.as_deref() == Some(link.as_str()) {
            return;
        }
        // Claim it BEFORE the native call. A push that the engine refuses is not retried on the
        // next frame -- retrying a refused push every frame is how a per-frame path turns one
        // failure into a permanent stall -- and the sequence number still moves on the next copy.
        *mirrored = Some(link.clone());
    }
    let outcome = {
        let utf16: Vec<u16> = link.encode_utf16().chain(core::iter::once(0)).collect();
        unsafe { set_text_input_02_990_text(base, menu_window, &utf16) }
    };
    if MIRROR_LOGS.fetch_add(1, Ordering::SeqCst) < MIRROR_LOG_LIMIT {
        match outcome {
            Ok(detail) => append_autoload_debug(format_args!(
                "system-quit-build-url: mirrored the copied link into the open field ({} chars, clipboard seq={sequence}) {detail}",
                link.chars().count()
            )),
            Err(error) => append_autoload_debug(format_args!(
                "system-quit-build-url: could NOT mirror the copied link into the open field window=0x{menu_window:x}; {error}"
            )),
        }
    }
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
    let text = {
        let guard = NEXT_TEXT
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        guard
            .as_deref()
            .unwrap_or(er_build_import::BUILD_URL_PREFIX)
            .to_owned()
    };
    let initial: Vec<u16> = text.encode_utf16().chain(core::iter::once(0)).collect();
    // Safety: menu-pump context, live Quit dialog.
    if unsafe { submit_build_url_keyboard(dialog, &initial) } {
        set_phase(EditorPhase::Open);
        // The field now holds this, so the mirror's first frames -- which will read the very
        // clipboard this text came from -- see no change and leave it alone. Baselining the
        // SEQUENCE here as well is what makes a re-open quiet: it means "nothing has been copied
        // since this field went up", so only a deliberate new copy can move the text under a
        // player who is mid-correction.
        reset_build_url_mirror();
        MIRRORED_SEQUENCE.store(clipboard_sequence() as usize, Ordering::SeqCst);
        *MIRRORED_TEXT
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(text);
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
            //
            // EXCEPT when there is nothing to correct. The live log caught this: a player cleared
            // the field, accepted, and the refusal re-opened with `initial_units=0` -- an empty box
            // with no prefix to build on and no way back to one short of backing out entirely.
            // Empty is not a typo, so an empty accept restarts from the clipboard-or-prefix the
            // first open would have used.
            *NEXT_TEXT
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner()) =
                Some(reopen_text_after_rejection(&text));
            set_phase(EditorPhase::Pending);
            append_autoload_debug(format_args!(
                "system-quit-build-url: REFUSED {text:?} ({}); re-opening the field ({reopens}/{MAX_REJECTED_REOPENS})",
                rejection.indicator()
            ));
        }
    }
}

/// What a refused accept should put back into the field.
///
/// Split out from [`on_accepted`] so the rule is testable on the host: the native re-open path it
/// feeds is not.
fn reopen_text_after_rejection(rejected: &str) -> String {
    if rejected.trim().is_empty() {
        build_url_initial_text()
    } else {
        rejected.to_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// THE `initial_units=0` STEP FROM THE LIVE LOG. A player cleared the field and accepted; the
    /// refusal carried the empty string back and re-opened an empty box. An empty accept is not a
    /// typo to correct in place, so it must restart from something usable.
    ///
    /// `build_url_initial_text` reads the clipboard, which a host test has no control over, so the
    /// assertion is the property that matters either way: what comes back is never empty, and it is
    /// either the bare prefix or an importable link.
    #[test]
    fn an_empty_accept_reopens_with_something_to_build_on() {
        for cleared in ["", "   ", "\t\n"] {
            let reopened = reopen_text_after_rejection(cleared);
            assert!(
                !reopened.trim().is_empty(),
                "an empty accept must not re-open an empty field, got {reopened:?}"
            );
            assert!(
                reopened == er_build_import::BUILD_URL_PREFIX
                    || er_build_import::validate_build_url(&reopened).is_ok(),
                "the re-open text must be the prefix or an importable link, got {reopened:?}"
            );
        }
    }

    /// A REAL typo is still carried back verbatim -- that is the whole point of the re-open, and
    /// replacing it with the prefix would throw away what the player typed.
    #[test]
    fn a_typo_is_carried_back_verbatim_for_correction() {
        for typo in [
            "https://er-build-planner.nyasu.business/?b=",
            "https://er-build-planner.nyasu.business/?b=NOT HEX",
            "nonsense",
        ] {
            assert_eq!(reopen_text_after_rejection(typo), typo);
        }
    }

    /// The mirror must not fire on the text it just put in. Seeding [`MIRRORED_TEXT`] at submit is
    /// what stops the open-time prefill from being re-pushed on frame one -- a push into a field
    /// the player may already be typing in.
    #[test]
    fn the_mirror_treats_the_submitted_text_as_already_shown() {
        reset_build_url_mirror();
        let prefilled = "https://er-build-planner.nyasu.business/?b=bc2a932db14675";
        *MIRRORED_TEXT
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(prefilled.to_owned());
        let mirrored = MIRRORED_TEXT
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        assert_eq!(mirrored.as_deref(), Some(prefilled));
        drop(mirrored);
        reset_build_url_mirror();
        assert!(
            MIRRORED_TEXT
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .is_none(),
            "a value carried across opens would make the next open's first paste look like a repeat"
        );
    }

    /// The unconditional window exists for Wine's asynchronous clipboard bridge, and it must cost a
    /// HANDFUL of clipboard locks, not one per frame. Reading on every frame of the window would be
    /// three dozen round trips to the X11 selection owner in half a second.
    #[test]
    fn the_unconditional_rereads_are_spaced_and_bounded() {
        let reread: Vec<usize> = (0..120)
            .filter(|f| mirror_rereads_unconditionally(*f, 0))
            .collect();
        assert_eq!(
            reread,
            vec![0, 10, 20],
            "the window must open with a read and then space them"
        );
        assert!(
            !mirror_rereads_unconditionally(MIRROR_UNCONDITIONAL_FRAMES, 0),
            "past the window, only a clipboard CHANGE may cost a read"
        );
    }

    /// A RE-open holds text the player edited into being wrong, and re-reading an unchanged
    /// clipboard there would replace their correction-in-progress with the very link they were
    /// editing away from. Only a deliberate new copy -- which moves the sequence number, not this
    /// rule -- may touch a re-opened field.
    #[test]
    fn a_reopen_never_rereads_an_unchanged_clipboard() {
        for reopens in 1..=MAX_REJECTED_REOPENS {
            for frame in 0..MIRROR_UNCONDITIONAL_FRAMES + 5 {
                assert!(
                    !mirror_rereads_unconditionally(frame, reopens),
                    "frame {frame} of re-open {reopens} must not overwrite the player's text"
                );
            }
        }
    }

    /// Both per-frame windows resolve and destroy two SceneObjProxies per application, so neither
    /// may be open-ended. A field can be up for minutes.
    #[test]
    fn the_per_frame_windows_are_bounded() {
        const {
            assert!(CARET_APPLY_FRAMES > 0 && CARET_APPLY_FRAMES <= 16);
            assert!(MIRROR_UNCONDITIONAL_FRAMES > 0 && MIRROR_UNCONDITIONAL_FRAMES <= 60);
            assert!(MIRROR_UNCONDITIONAL_STRIDE > 1);
        }
    }
}
