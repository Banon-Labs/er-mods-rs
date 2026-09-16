// Use an item the way a player does: cycle the quick-slot cursor onto it, then press use.
//
// # Why this replaces every synthetic drive
//
// The previous drives wrote `menuGaitemUseState` by hand and answered
// `CS::PlayerIns::GetSelectedQuickSlotItemId` with an id of our choosing. Both play the animation
// and latch `ChrIns+0x160`, and neither opens Seamless's popup -- measured twice on 2026-09-16,
// once with our flag writes and once with none.
//
// It turns out none of that was necessary. A read of the ten quick slots on the live character
// found the Challenger's Lynchpin ALREADY in slot 1 (`0x407fde63`), so the item does not need
// equipping, substituting or pinning: it needs SELECTING. Cycling the cursor is what a player
// does, it writes nothing, and it leaves no difference between the driven use and a real one for
// Seamless to notice.
//
// The game's own getter is the oracle for where the cursor is, so the cycle is closed-loop rather
// than a guess at how many presses are needed.
const WORLD_CHR_MAN = ptr('0x143d69ff8');
const MAIN_PLAYER = 0x1e508;
const GET_SELECTED_QUICK_SLOT_ITEM_ID = ptr('0x140657410');
const CHR_INS_QUEUED_USE_ITEM = 0x160;
// The TAE event 65 consume count. It is a COUNTER and does not self-clear, so a reading only means
// something as a delta across the press.
const CHR_INS_CONSUME_COUNT = 0x168;

function player () {
  const world = WORLD_CHR_MAN.readPointer();
  if (world.isNull()) return null;
  const p = world.add(MAIN_PLAYER).readPointer();
  return p.isNull() ? null : p;
}

rpc.exports = {
  // What the cursor is on right now, asked of the game rather than tracked by us.
  selected () {
    const p = player();
    if (p === null) return null;
    const fn = new NativeFunction(GET_SELECTED_QUICK_SLOT_ITEM_ID, 'pointer', ['pointer', 'pointer']);
    const out = Memory.alloc(4);
    out.writeS32(-1);
    fn(p, out);
    const id = out.readS32();
    return { id, hex: '0x' + (id >>> 0).toString(16) };
  },

  // The two fields that say whether a use actually happened, read together because the queue
  // latching without the consume moving is exactly the failure this is chasing.
  useState () {
    const p = player();
    if (p === null) return null;
    return {
      queued: '0x' + p.add(CHR_INS_QUEUED_USE_ITEM).readU32().toString(16),
      consumed: p.add(CHR_INS_CONSUME_COUNT).readU32(),
    };
  },

  // Put the queued-use slot back to rest.
  //
  // `ChrIns+0x160` holds the item id of a use the engine has taken and `0xffffffff` at rest. A
  // drive that is killed mid-use leaves an id latched there, and the engine then refuses the next
  // use -- which is why this repo's recipe says to relaunch between attempts. Writing the rest
  // value is the same end state, without the relaunch.
  clearQueued () {
    const p = player();
    if (p === null) return { ok: false, why: 'no player' };
    const before = p.add(CHR_INS_QUEUED_USE_ITEM).readU32();
    p.add(CHR_INS_QUEUED_USE_ITEM).writeU32(0xffffffff);
    return { ok: true, before: '0x' + before.toString(16),
      after: '0x' + p.add(CHR_INS_QUEUED_USE_ITEM).readU32().toString(16) };
  },
};
console.log('native-quickslot-cycle: ready (reads only; the caller presses)');
