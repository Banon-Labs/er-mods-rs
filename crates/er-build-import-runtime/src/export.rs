//! Turning the LIVE character into an `er-build-planner` share link.
//!
//! The mirror image of [`crate::request`]/[`crate::tick`], and deliberately the same SHAPE: a press
//! latches a request, the game-thread step does the part that must touch game memory, and a worker
//! does the part that must not run on the game thread. What differs is which half is slow.
//!
//! * The IMPORTER's slow half is at the FRONT -- a blocking HTTPS GET -- so its worker runs first
//!   and its game-thread step applies the result.
//! * The EXPORTER has no network at all. The `?i=` share format carries the whole build in the URL
//!   (LZUTF8 over base64 over JSON), so nothing is fetched and no account is minted on someone
//!   else's free hobby service. Its slow half is at the BACK: `ShellExecuteW` spawns
//!   `winebrowser`, which spawns `xdg-open`, which spawns a browser. So the game-thread step runs
//!   FIRST -- reading the character is a few dozen native getter calls, microseconds -- and the
//!   worker takes the finished document away to encode, copy and open.
//!
//! # The in-flight latch must lose to the player
//!
//! A press while an export is running has to be refused, or two workers race for the clipboard. But
//! a latch that can only say "busy" is a latch that can strand the row: the link field next door
//! went dead for three consecutive presses because an active-flag survived a rebuilt dialog with
//! nothing left alive to clear it.
//!
//! So [`export_latch_is_stale`] answers from LIVE EVIDENCE, never from a flag alone:
//!
//! * [`Phase::Reading`] is claimed by the game-thread step, which runs every frame. A request that
//!   is still `Reading` after the step has run [`STALE_TICKS`] times was not picked up and never
//!   will be -- the tick's own counter is the witness, not a clock.
//! * [`Phase::Opening`] belongs to a worker thread that increments [`WORKERS_ALIVE`] on entry and
//!   decrements it from a `Drop` guard, so a panicking worker still releases it. `Opening` with no
//!   worker alive is a phase nobody owns.
//!
//! When it cannot prove the export is live, the press WINS and the latch is cleared. The worst case
//! of being wrong that way is two browser tabs; the worst case of the other way is a row that never
//! works again until the game restarts.

use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};

use crate::read_character::CharacterRead;

/// How many game-thread ticks may pass with a request unclaimed before it is considered stranded.
///
/// This is a count of ticks that DID run, not a duration: the step re-checks its preconditions
/// every frame and claims any `Reading` request whose preconditions hold, so a request that has
/// survived this many of its own opportunities is one the step is refusing, not one it has not
/// reached yet. Generous because the first frames after a menu opens are the busiest.
pub const STALE_TICKS: usize = 240;

/// Where an export is.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[repr(usize)]
pub enum Phase {
    /// Nothing in flight.
    Idle = 0,
    /// A press has asked; the game-thread step has not picked it up yet.
    Reading = 1,
    /// A worker holds the document and is encoding / copying / opening.
    Opening = 2,
    /// The last export finished. Not "busy".
    Done = 3,
    /// The last export failed; [`take_error`] says why, once.
    Failed = 4,
}

impl Phase {
    fn from_code(code: usize) -> Phase {
        match code {
            1 => Phase::Reading,
            2 => Phase::Opening,
            3 => Phase::Done,
            4 => Phase::Failed,
            _ => Phase::Idle,
        }
    }

    /// Whether a press may claim the machine from here.
    fn accepts_request(self) -> bool {
        matches!(self, Phase::Idle | Phase::Done | Phase::Failed)
    }
}

static PHASE: AtomicUsize = AtomicUsize::new(Phase::Idle as usize);
/// Ticks the game-thread step has run, ever. The witness `Reading` staleness is measured against.
static TICKS: AtomicUsize = AtomicUsize::new(0);
/// [`TICKS`] as it stood when the pending request was latched.
static REQUEST_TICK: AtomicUsize = AtomicUsize::new(0);
/// Workers currently inside [`spawn_worker`]. Released by a `Drop` guard, so a panic still clears
/// it -- which is what makes "Opening with no worker" a sound staleness test rather than a guess.
static WORKERS_ALIVE: AtomicUsize = AtomicUsize::new(0);
/// The finished URL, for the caller that wants to log or show it.
static LAST_URL: Mutex<Option<String>> = Mutex::new(None);
/// Why the last export failed.
static LAST_ERROR: Mutex<Option<String>> = Mutex::new(None);

/// Current phase.
pub fn phase() -> Phase {
    Phase::from_code(PHASE.load(Ordering::SeqCst))
}

/// TAKE the reason the last export failed, clearing it. Taken rather than peeked for the same
/// reason the importer's is: the failure is asynchronous, so the only way anyone learns of it is by
/// polling, and a peek would re-report one failure on every frame.
pub fn take_error() -> Option<String> {
    LAST_ERROR.lock().ok().and_then(|mut slot| slot.take())
}

/// TAKE the URL the last export produced.
pub fn take_url() -> Option<String> {
    LAST_URL.lock().ok().and_then(|mut slot| slot.take())
}

fn set_error(reason: String) {
    crate::log_line(&format!("[build-export] FAILED: {reason}"));
    if let Ok(mut slot) = LAST_ERROR.lock() {
        *slot = Some(reason);
    }
    PHASE.store(Phase::Failed as usize, Ordering::SeqCst);
}

/// Whether a busy-looking latch cannot be shown to be live.
///
/// Returns `true` ONLY when there is positive evidence that nothing owns the phase. See the module
/// header for why the answer defaults toward letting the player through.
pub fn export_latch_is_stale() -> bool {
    match phase() {
        // A request nobody claimed, after the claimer had this many chances.
        Phase::Reading => {
            TICKS
                .load(Ordering::SeqCst)
                .saturating_sub(REQUEST_TICK.load(Ordering::SeqCst))
                >= STALE_TICKS
        }
        // A phase whose only owner is a thread that is not running.
        Phase::Opening => WORKERS_ALIVE.load(Ordering::SeqCst) == 0,
        // Not busy at all.
        _ => false,
    }
}

/// One line describing the latch, for the debug log. A refusal that does not say what it was
/// refusing on behalf of is a refusal nobody can diagnose.
pub fn export_latch_state() -> String {
    format!(
        "phase={:?} ticks={} request_tick={} workers_alive={}",
        phase(),
        TICKS.load(Ordering::SeqCst),
        REQUEST_TICK.load(Ordering::SeqCst),
        WORKERS_ALIVE.load(Ordering::SeqCst),
    )
}

/// Force the machine back to idle. Called when a press finds a stale latch, and when the Quit
/// dialog is rebuilt.
pub fn reset() {
    PHASE.store(Phase::Idle as usize, Ordering::SeqCst);
    REQUEST_TICK.store(TICKS.load(Ordering::SeqCst), Ordering::SeqCst);
}

/// Why [`request`] refused.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum RequestError {
    /// An export is genuinely still running -- proven, not assumed.
    Busy,
}

impl core::fmt::Display for RequestError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            RequestError::Busy => write!(f, "an export is already running"),
        }
    }
}

/// Ask for an export. Returns as soon as the request is latched; the game thread must keep calling
/// [`tick`] for anything to happen.
///
/// Safe to call from any thread, including a menu action handler: nothing here touches game state.
pub fn request() -> Result<(), RequestError> {
    if !phase().accepts_request() {
        return Err(RequestError::Busy);
    }
    if let Ok(mut slot) = LAST_ERROR.lock() {
        *slot = None;
    }
    REQUEST_TICK.store(TICKS.load(Ordering::SeqCst), Ordering::SeqCst);
    PHASE.store(Phase::Reading as usize, Ordering::SeqCst);
    Ok(())
}

/// What one completed export did. Every field is measured from the document that was actually
/// encoded, never from a call that returned.
#[derive(Clone, Debug, Default)]
pub struct ExportReport {
    /// The character's name, as the link will carry it.
    pub character: String,
    /// Equipped armaments, armour pieces, talismans and memorised spells that made it in.
    pub armaments: usize,
    pub protectors: usize,
    pub talismans: usize,
    pub spells: usize,
    /// Equipment slots holding an item whose id resolved to no name, so a short build says so.
    pub unnamed: usize,
    /// Characters in the finished URL.
    pub url_len: usize,
    /// Whether the URL reached the clipboard, and whether a browser accepted it.
    pub clipboard: bool,
    pub opened: bool,
}

impl ExportReport {
    /// One line for a menu help field or a log.
    pub fn summary(&self) -> String {
        format!(
            "{} armaments, {} armour, {} talismans, {} spells -> {} char link{}{}",
            self.armaments,
            self.protectors,
            self.talismans,
            self.spells,
            self.url_len,
            if self.clipboard { ", copied" } else { "" },
            if self.opened { ", opened" } else { "" },
        )
    }
}

/// What the worker must be able to do, supplied by the caller.
///
/// The read and the encode belong to this crate; the clipboard and the browser belong to Windows,
/// and this crate is linked into more than one DLL. Handing them in as function pointers keeps the
/// OS surface at the call site, where the `windows` crate features that back it are already
/// declared, instead of forcing every consumer to carry them.
#[derive(Clone, Copy)]
pub struct Sinks {
    /// Put the URL on the clipboard. Returns whether it landed.
    pub clipboard: fn(&str) -> bool,
    /// Hand the URL to the OS to open. Returns whether it was accepted.
    pub open: fn(&str) -> bool,
}

/// One frame of the exporter. Does nothing until a press has asked AND the game can be read.
///
/// Returns the report only once the whole export is DONE, which is a worker later -- so a caller
/// polling this sees `None` for the frames in between rather than a half-finished answer.
///
/// # Safety
///
/// Game task thread. Every step is precondition-checked, but the thread itself is not something
/// this function can verify.
pub unsafe fn tick(sinks: Sinks) -> Option<ExportReport> {
    TICKS.fetch_add(1, Ordering::SeqCst);
    if phase() != Phase::Reading {
        return None;
    }
    if !crate::catalog::params_ready() {
        return None;
    }
    // Safety: game thread; the helper is fault-checked and returns false at the title screen.
    if !unsafe { crate::grant::player_present() } {
        return None;
    }
    let msg = crate::catalog::msg_repository()?;
    // Safety: game thread, character present.
    let egd = unsafe { crate::grant::equip_game_data() }?;
    let module_base = crate::module_base();

    // Safety: game task thread, params streamed, character in the world -- all three just checked.
    let Some(read) = (unsafe { crate::read_character::read_character(module_base, msg, egd) })
    else {
        set_error("the character could not be read".to_owned());
        return None;
    };
    crate::log_line(&format!(
        "[build-export] read character={:?} class={:?} armaments={} armour={} talismans={} \
         spells={} tears={} rune={:?} 2h={} flasks={}+{} upgrade={:?} unnamed={}",
        read.name,
        read.character_class,
        read.armaments.len(),
        read.protectors.len(),
        read.talismans.len(),
        read.spells.len(),
        read.crystal_tears.iter().flatten().count(),
        read.great_rune,
        read.two_handing,
        read.flask_crimson,
        read.flask_cerulean,
        read.weapon_upgrade,
        read.unnamed_slots,
    ));

    PHASE.store(Phase::Opening as usize, Ordering::SeqCst);
    spawn_worker(read, sinks);
    None
}

/// Hand the finished document to a worker for the parts that must not run on the game thread.
fn spawn_worker(read: CharacterRead, sinks: Sinks) {
    /// Releases the alive count on EVERY exit path, panic included. Without this a panicking worker
    /// would leave `Opening` looking owned forever, which is exactly the stranded-latch failure the
    /// module header exists to prevent.
    struct Alive;
    impl Drop for Alive {
        fn drop(&mut self) {
            WORKERS_ALIVE.fetch_sub(1, Ordering::SeqCst);
        }
    }

    WORKERS_ALIVE.fetch_add(1, Ordering::SeqCst);
    std::thread::spawn(move || {
        let _alive = Alive;
        if std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| export_inner(read, sinks)))
            .is_err()
        {
            set_error("export worker PANICKED".to_owned());
        }
    });
}

/// The encode, the clipboard and the browser. Runs on the worker; touches no game state.
fn export_inner(read: CharacterRead, sinks: Sinks) {
    let doc = crate::export_doc::document_from(&read);
    let url = er_build_export::share_url(&doc);
    let mut report = ExportReport {
        character: read.name.clone(),
        armaments: read.armaments.len(),
        protectors: read.protectors.len(),
        talismans: read.talismans.len(),
        spells: read.spells.len(),
        unnamed: read.unnamed_slots,
        url_len: url.chars().count(),
        ..ExportReport::default()
    };
    crate::log_line(&format!(
        "[build-export] encoded {} characters for {:?}",
        report.url_len, report.character
    ));
    // The whole URL, on its own line, so the offline oracle can decode exactly what the game
    // produced instead of a description of it.
    crate::log_line(&format!("[build-export] URL {url}"));

    report.clipboard = (sinks.clipboard)(&url);
    report.opened = (sinks.open)(&url);
    crate::log_line(&format!(
        "[build-export] clipboard={} browser={}",
        report.clipboard, report.opened
    ));

    if let Ok(mut slot) = LAST_URL.lock() {
        *slot = Some(url);
    }
    if !report.opened {
        // A link that was built but never reached a browser is a FAILURE the player must be told
        // about -- silently succeeding here is how "I pressed it and nothing happened" happens.
        set_error(format!(
            "the link was built ({} characters){} but no browser would open it",
            report.url_len,
            if report.clipboard {
                " and copied to the clipboard"
            } else {
                ""
            },
        ));
    } else {
        PHASE.store(Phase::Done as usize, Ordering::SeqCst);
    }
    if let Ok(mut slot) = LAST_REPORT.lock() {
        *slot = Some(report);
    }
}

/// The finished report, waiting for whichever thread asks next.
static LAST_REPORT: Mutex<Option<ExportReport>> = Mutex::new(None);

/// TAKE the report of the last completed export.
pub fn take_report() -> Option<ExportReport> {
    LAST_REPORT.lock().ok().and_then(|mut slot| slot.take())
}
