// Does er-invasion-warp's near+far handoff actually press the pad, and does Seamless then query
// Steam? Both counters in one attach, because they have to be read against each other.
//
// # Why this exists
//
// Driving the Challenger's Lynchpin from OUTSIDE -- pin it, wait, hold pad A for half a second --
// produces `RequestLobbyList` and five filter calls, reproduced twice. The same item driven from
// inside the DLL, with a live pin and the same wall-clock timing, produces nothing on any of the
// 38 matchmaking slots. Three explanations were tried and each was refuted by its own measurement:
// the pin was alive at the press, the hold was a real 500ms, and the character-finished signal can
// never arrive because `ChrIns+0x160` does not clear.
//
// So the question stopped being "what timing" and became "does the press happen at all". This
// answers that directly: every call into the product's pad export, with its mask and the thread it
// came from, beside the lobby slots. A press that never reaches the export makes every timing
// theory moot; a press that reaches it with mask 0x1000 moves the search past the export entirely.
const PAD_A = 0x1000;
const ERSC_MATCHMAKING_SLOT = 0x21b610;
const LOBBY_NAMES = {
  4: 'RequestLobbyList',
  5: 'AddRequestLobbyListStringFilter',
  13: 'CreateLobby',
  14: 'JoinLobby',
};

const out = { pad: { resolved: false, calls: 0, masks: {}, threads: {} }, lobby: { hooked: 0, hits: {} } };

const product = Process.findModuleByName('er_quickload.dll');
if (product !== null) {
  const address = product.getExportByName('er_quickload_hold_xinput_pad');
  if (address !== null && !address.isNull()) {
    out.pad.resolved = true;
    Interceptor.attach(address, {
      onEnter (args) {
        out.pad.calls += 1;
        const mask = args[0].toUInt32() & 0xffff;
        const key = mask === PAD_A ? '0x1000 A' : `0x${mask.toString(16)}`;
        out.pad.masks[key] = (out.pad.masks[key] || 0) + 1;
        const tid = Process.getCurrentThreadId();
        out.pad.threads[tid] = (out.pad.threads[tid] || 0) + 1;
      },
    });
  }
}

// The whole vtable, not the four slots of interest: a sweep that hooks everything carries its own
// control, and a four-slot zero cannot tell "not called" from "not hooked".
const ersc = Process.findModuleByName('ersc.dll');
if (ersc !== null) {
  const iface = ersc.base.add(ERSC_MATCHMAKING_SLOT).readPointer();
  const vtable = iface.readPointer();
  for (let slot = 0; slot < 40; slot++) {
    let fn;
    try { fn = vtable.add(slot * Process.pointerSize).readPointer(); } catch (e) { break; }
    if (fn.isNull() || Process.findModuleByAddress(fn) === null) continue;
    const label = LOBBY_NAMES[slot] ? `${slot} ${LOBBY_NAMES[slot]}` : `${slot}`;
    try {
      Interceptor.attach(fn, { onEnter () { out.lobby.hits[label] = (out.lobby.hits[label] || 0) + 1; } });
      out.lobby.hooked += 1;
    } catch (e) { /* slots can share an address; a second hook there is refused */ }
  }
}

rpc.exports = { counts () { return out; } };
console.log(`handoff-trace: pad export resolved=${out.pad.resolved}, ${out.lobby.hooked} lobby slot(s) hooked`);
