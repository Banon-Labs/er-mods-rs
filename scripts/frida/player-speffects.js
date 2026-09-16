// Every SpEffect id currently on the local player.
//
// The chain is the game's own, read out of `CS::ChrIns::HasSpecialEffectId` at `0x1404fad30`:
//
//   container = [ChrIns + 0x178]
//   head      = [container + 0x08]
//   id        = [entry + 0x08]
//   next      = [entry + 0x30]
//
// This is the oracle for whether the Festering Bloody Finger is currently active: the item is a
// toggle, and neither `ChrIns+0x160` (a queue that never self-clears) nor `ChrIns+0x168` (a TAE 65
// consume counter) reports a toggle's state.  An id that appears when the item is on and goes when
// it is off names the state directly, with no inference.
const WORLD_CHR_MAN = ptr('0x143d69ff8');
const MAIN_PLAYER = 0x1e508;
const CHR_INS_SPECIAL_EFFECT = 0x178;
const SPECIAL_EFFECT_HEAD = 0x08;
const ENTRY_ID = 0x08;
const ENTRY_NEXT = 0x30;
const WALK_LIMIT = 512;

function player () {
  const world = WORLD_CHR_MAN.readPointer();
  if (world.isNull()) return null;
  const p = world.add(MAIN_PLAYER).readPointer();
  return p.isNull() ? null : p;
}

rpc.exports = {
  ids () {
    const p = player();
    if (p === null) return null;
    try {
      const container = p.add(CHR_INS_SPECIAL_EFFECT).readPointer();
      if (container.isNull()) return null;
      let entry = container.add(SPECIAL_EFFECT_HEAD).readPointer();
      const ids = [];
      for (let i = 0; i < WALK_LIMIT && !entry.isNull(); i++) {
        ids.push(entry.add(ENTRY_ID).readS32());
        entry = entry.add(ENTRY_NEXT).readPointer();
      }
      return ids;
    } catch (e) { return null; }
  },
};
console.log('player-speffects: ready');
