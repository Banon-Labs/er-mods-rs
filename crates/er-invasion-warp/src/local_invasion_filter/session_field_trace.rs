//! The per-frame session field-write tracer.
//!
//! Lifted out of `local_invasion_filter.rs` on 2026-09-15 when that file crossed the 3200-line
//! hard limit `scripts/check-rust-file-sizes.py` enforces. It is the cleanest seam in the
//! parent: a diagnostic that reads a fixed window of the session object every frame and reports
//! which words changed, called from exactly one place and depended on by nothing else.
//!
//! It stays here rather than in `er-invasion-warp-core` because every line of it is a read of
//! live game memory through a pointer Seamless owns; there is nothing host-testable in the body.
//! The two constants that describe the window are host-visible on purpose, and the tests that
//! prove the window still covers every known field live in the parent's `tests` module.

use super::*;

/// The window of the session object that is watched for VM writes.
///
/// Chosen to span every field the static read identified plus the unexplained space between them:
/// state `+0x110`, the lobby id `+0x178` and owner `+0x180`, the per-offer block `+0x190..0x227`,
/// the `+0x1D4` / `+0x1F0` latches Seek writes, and the `+0x229` flag the lobby key mixes in.
/// Deliberately not `cfg(windows)`: it is plain arithmetic, and the tests that prove the window
/// still covers every known field have to run on the host build like every other test here.
pub(super) const SESSION_WATCH_BEGIN: usize = 0x100;
pub(super) const SESSION_WATCH_WORDS: usize = 0x30; // 0x30 * 8 = 0x180 bytes -> 0x100..0x280

/// Previous snapshot, and which session it came from.
#[cfg(windows)]
static SESSION_SNAPSHOT: Mutex<Option<(usize, [u64; SESSION_WATCH_WORDS])>> = Mutex::new(None);

/// How many field-change lines have been written, so a churning field cannot flood the log.
#[cfg(windows)]
static SESSION_FIELD_LINES: AtomicUsize = AtomicUsize::new(0);
/// The cap. Generous enough to cover a whole invasion sequence, small enough to stay readable.
#[cfg(windows)]
const SESSION_FIELD_LINE_BUDGET: usize = 400;

/// Report which session fields changed since the last frame, with the state they changed under.
///
/// # Why this exists
///
/// States `0x0E`, `0x11`, `0x12`, `0x13` and `0x14` are written by no instruction in ersc's
/// readable code -- a byte-anchored scan for `C7 /0 disp32=0x110 imm32` finds only
/// `{0,1,3,6,9,0xD,0x22,0x23}`, and the sole register-sourced write produces `0x0C`/`0x15`. The
/// rest come out of the Themida VM. Reading that code is not available: a live dump of the module
/// showed `.themida` is 99.68% byte-identical to disk with unchanged entropy, so the original
/// instructions never exist in memory to be recovered.
///
/// What is available is the effect. Every field the VM writes is written into an object this
/// module already holds a pointer to, so diffing that object per frame maps the state machine
/// empirically -- which fields move together, which precede a transition, which carry a
/// destination -- without reading a single VM instruction.
///
/// Pure observation: it reads and logs, and writes nothing back.
///
/// # Safety
/// Game task thread; every read is fault-closed and the window is a fixed span of an object the
/// caller already validated.
#[cfg(windows)]
pub(super) fn trace_session_field_writes(seamless: SeamlessSession) {
    let session = seamless.session;
    if SESSION_FIELD_LINES.load(Ordering::SeqCst) >= SESSION_FIELD_LINE_BUDGET {
        return;
    }
    let mut current = [0_u64; SESSION_WATCH_WORDS];
    for (index, slot) in current.iter_mut().enumerate() {
        let at = session + SESSION_WATCH_BEGIN + index * 8;
        // A fault-closed read that fails leaves the slot zero. That could masquerade as a change,
        // so a failed read abandons the whole snapshot rather than inventing a transition.
        let Some(value) = (unsafe { er_game_base::mem::safe_read_usize(at) }) else {
            return;
        };
        *slot = value as u64;
    }

    let mut guard = match SESSION_SNAPSHOT.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    let previous = match guard.as_ref() {
        // A different session object is a different machine; its first frame is a baseline, not a
        // set of changes.
        Some((owner, _)) if *owner != session => None,
        Some((_, snapshot)) => Some(*snapshot),
        None => None,
    };
    *guard = Some((session, current));
    drop(guard);

    let Some(previous) = previous else {
        return;
    };
    let changed: Vec<(usize, u64, u64)> = (0..SESSION_WATCH_WORDS)
        .filter(|index| previous[*index] != current[*index])
        .map(|index| {
            (
                SESSION_WATCH_BEGIN + index * 8,
                previous[index],
                current[index],
            )
        })
        .collect();
    if changed.is_empty() {
        return;
    }
    let state =
        unsafe { er_game_base::mem::safe_read_i32(session + seamless.abi.session_state_offset) }
            .unwrap_or(-1);
    // `+0x238` alone is not a field change worth a line: it is a clock, and it ticks once a frame.
    //
    // Measured on run br-20260910-012230-666e, where the second cancel stuck at 0x23 and this
    // logger wrote one line per tick for the whole wait. The upper half of the qword decodes as an
    // f64 running 0.386 -> 0.443 -> ... -> 14.1 seconds and still climbing, one step per tick, so
    // the "change" is a cooldown advancing normally rather than anything a reader needs told 500
    // times. Reporting it as a cooldown once a second says strictly more in 1/40th the lines.
    if changed.len() == 1 && changed[0].0 == SESSION_COOLDOWN_OFFSET {
        let seconds = f64::from_bits(changed[0].2 & 0xffff_ffff_0000_0000);
        let whole = seconds as usize;
        if COOLDOWN_LAST_WHOLE_SECOND.swap(whole, Ordering::SeqCst) != whole {
            // The meaning is per-state and must not be asserted across all of them. This line
            // first fired at state 0x16 saying "this is the wait the player sees after a cancel",
            // which is false there -- 0x16 is being in an invasion, and its clock is how long the
            // player has been fighting.
            let meaning = if state == seamless.abi.state_cancelling as i32 {
                "an armed re-invade waits for idle, so this clock is the wait after a cancel"
            } else if state == seamless.abi.state_searching as i32 {
                "this clock is how long the search has been running"
            } else {
                "what this clock measures in this state has not been established"
            };
            crate::standalone_log(format_args!(
                "local-invasion: the session has been in state {state:#04x} for {whole}s -- \
                 `session+0x{SESSION_COOLDOWN_OFFSET:x}` is a clock, not a field change, and it \
                 advances once a frame. {meaning}."
            ));
        }
        return;
    }
    let line = SESSION_FIELD_LINES.fetch_add(1, Ordering::SeqCst) + 1;
    crate::standalone_log(format_args!(
        "local-invasion: session fields changed at state {state:#04x} -- {changed:x?} \
         (offset, before, after). These are writes this DLL did not make; the ones at offsets with \
         no readable writer came from the Themida VM. Line {line}/{SESSION_FIELD_LINE_BUDGET}."
    ));
    if line == SESSION_FIELD_LINE_BUDGET {
        crate::standalone_log(format_args!(
            "local-invasion: session field tracing has hit its {SESSION_FIELD_LINE_BUDGET}-line \
             budget and will stay quiet from here. Raise SESSION_FIELD_LINE_BUDGET if a longer \
             sequence is needed; the cap exists so one churning field cannot bury the run."
        ));
    }
}

#[cfg(not(windows))]
pub(super) fn trace_session_field_writes(_session: SeamlessSession) {}
