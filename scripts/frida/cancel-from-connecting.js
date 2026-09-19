// Cancel a Seamless search that is sitting at `0x12`, which `drive-cancel-now.js` cannot reach.
//
// # Why that agent misses
//
// `drive-cancel-now.js` hooks the invade action `ersc+0x25850` and cancels in its `onLeave`,
// on the reasoning that the action is the sole writer of `0x0e` into `session+0x150`. Measured
// 2026-09-17 on run `br-20260917-234208-1e25`: the agent armed, reported `state_now: 18` (`0x12`),
// and then never fired across repeated `0x12 -> 0x0e -> 0x0f -> 0x12` cycles logged by the DLL. So
// the loop returns to `0x0e` without entering the invade action at all.
//
// That matches the image. `ersc-2.0.1.runtime.bin` carries 96 writers of `[reg+0x150]` and 87 of
// them store from a register -- `0x1800733d6 mov dword ptr [rdi + 0x150], eax` among them. Only
// the immediate stores were ever scanned, which is why `0x0e` looked like it had one writer.
//
// # What this does instead
//
// It does not wait for a state at all. `ersc+0x258d0` is the cancel action, and this repo has
// already established that the cancel row is offered at `0x12` -- `cancel_row_offered(0x12)` is
// true in `local_invasion_filter::lock_report`, and that predicate is ERSC's own hide rule. So a
// cancel driven at `0x12` is a button the player is being shown, not a poke at an unknown state.
//
// The session comes from the shim `er_invasion_warp` already keeps: the cancel action reads
// `rcx+0x58` and nothing else, so any box carrying the session pointer there serves. This agent
// allocates its own rather than relying on a stale menu-object address from a previous process,
// which is the other reason the seeded read above reports `unreadable` more often than not.
//
// Read-only until it fires: it locates the session, reports what it found, and calls cancel once.
'use strict';

const CANCEL = 0x258d0;
const STATE = 0x150;
const GUARD = 0x14c;
const MUTEX = 0x100;
const CANCELLING = 0x23;

// States ERSC draws its own Cancel row for, mirrored from `lock_report::cancel_row_offered`.
const CANCELLABLE = [0x0e, 0x0f, 0x12];

const ersc = Process.findModuleByName('ersc.dll');
if (ersc === null) {
  send({ kind: 'error', message: 'ersc.dll not loaded' });
} else {
  const cancelFn = new NativeFunction(ersc.base.add(CANCEL), 'void', ['pointer']);

  // A session is self-identifying: a state code this build defines at `+0x150`, and a std::mutex
  // shaped word at `+0x100`. Scanning for that pair is what `local_invasion_filter::session_scan`
  // does; here the candidate is handed in by the caller instead, so nothing is scanned.
  function report(session, note) {
    let state = null;
    let guard = null;
    try {
      state = session.add(STATE).readU32();
      guard = session.add(GUARD).readU32();
    } catch (error) {
      send({ kind: 'unreadable', session: session.toString(), error: error.message });
      return null;
    }
    send({ kind: 'session', session: session.toString(), state: state, guard: guard, note: note });
    return state;
  }

  rpc.exports = {
    // Drive the cancel against a session address the caller already knows -- the DLL logs one on
    // every heartbeat as `ersc_session=0x...`.
    cancel: function (sessionHex) {
      const session = ptr(sessionHex);
      const state = report(session, 'before');
      if (state === null) {
        return { ok: false, why: 'session unreadable' };
      }
      if (CANCELLABLE.indexOf(state) === -1) {
        return { ok: false, why: 'state ' + state + ' is not one ERSC offers Cancel for' };
      }
      // The shim: a box whose `+0x58` is the session, which is the only field the action reads.
      const shim = Memory.alloc(0x80);
      shim.add(0x58).writePointer(session);
      cancelFn(shim);
      const after = session.add(STATE).readU32();
      send({ kind: 'cancelled', state_before: state, state_after: after, proved: after === CANCELLING });
      return { ok: true, state_before: state, state_after: after, proved: after === CANCELLING };
    },

    look: function (sessionHex) {
      return report(ptr(sessionHex), 'look');
    },
  };

  send({ kind: 'ready', cancel_action: 'ersc+0x' + CANCEL.toString(16), mutex_offset: MUTEX });
}
