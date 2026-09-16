// Controller input measured in GAME FRAMES, not in seconds.
//
// # Why this exists
//
// A driver that holds a button for `time.sleep(0.4)` is guessing at how many frames the game will
// see, and that guess changes with frame rate and load. It is also sleep-as-synchronization, which
// this repo bans outright (`scripts/check-no-timeouts.py`). The fix is to count the thing that
// actually matters: the game rebuilds its per-key input array every frame, in
// `FD4PadManager`'s builder at rva 0x240e70 (RE'd in `crates/er-input-harness/src/pad_inject.rs`,
// byte-checked on 1.17). Hooking it gives an exact frame tick with no timer of any kind.
//
// A tap is then: assert the mask for `holdFrames` frames, clear it for `gapFrames`, and `send()` a
// completion the caller blocks on. The caller never sleeps and never polls -- it waits on a real
// event, with its own hard cap.
const BUILDER_A_RVA = 0x240e70;

function follow (address) {
  return address.readU8() === 0xe9
    ? address.add(5).add(address.add(1).readS32())
    : address;
}

// Measured 2026-09-16: on a run with no controller attached, `FD4PadManager`'s builders are never
// called at all (0 hits in 3s, both Arxan-stubbed), so they are not a tick here. `XInputGetState`
// is, at ~82 calls/second, and it is the better unit anyway -- it is the exact call that samples
// the pad state this module injects, so a hold counted in these is a hold the game actually read.
const XINPUT = 'XINPUT1_4.dll';
const XINPUT_READ = 'XInputGetState';
const game = Process.findModuleByName('eldenring.exe');
const quickload = Process.findModuleByName('er_quickload.dll');
if (game === null) throw new Error('eldenring.exe is not present');
if (quickload === null) throw new Error('er_quickload.dll is not loaded; no pad injection is possible');
const hold = new NativeFunction(
  quickload.getExportByName('er_quickload_hold_xinput_pad'), 'void', ['uint16', 'int16', 'int16']);

let frames = 0;
// One request at a time. The game thread owns it once it is set; the caller only ever reads back
// the completion that the game thread sends.
let pending = null;

const xinput = Process.findModuleByName(XINPUT);
const tick = xinput === null
  ? follow(game.base.add(BUILDER_A_RVA))
  : follow(xinput.getExportByName(XINPUT_READ));

Interceptor.attach(tick, {
  onEnter () {
    frames += 1;
    if (pending === null) return;
    if (pending.held < pending.holdFrames) {
      pending.held += 1;
      hold(pending.mask, pending.lx, pending.ly);
      return;
    }
    if (pending.gapped < pending.gapFrames) {
      pending.gapped += 1;
      hold(0, 0, 0);
      return;
    }
    const done = pending;
    pending = null;
    hold(0, 0, 0);
    send({ kind: 'tap-done', id: done.id, frames: frames });
  },
});

rpc.exports = {
  frames: function () { return frames; },
  // Returns immediately; the caller waits for the matching `tap-done`.
  tap: function (id, mask, holdFrames, gapFrames, lx, ly) {
    if (pending !== null) return { ok: false, why: 'a tap is already in flight' };
    pending = {
      id: id,
      mask: mask,
      lx: lx || 0,
      ly: ly || 0,
      holdFrames: holdFrames,
      gapFrames: gapFrames,
      held: 0,
      gapped: 0,
    };
    return { ok: true };
  },
  release: function () { pending = null; hold(0, 0, 0); return { ok: true }; },
};
console.log('pad-frames: ready (taps are counted in game frames; no timers)');
