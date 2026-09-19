// Is the Festering Bloody Finger ON right now, and therefore which prompt will using it raise?
//
// # The discriminator, measured 2026-09-16
//
// SpEffect **541** is the item's active state. In one run its presence went with the raiser
// reporting message `20000011` kind `1` -- "Cancel invasion of other world?" -- and confirming that
// prompt removed 541 from the player's list. So:
//
//   541 present  -> using the item raises 20000011 (kind 1), and confirming TURNS IT OFF
//   541 absent   -> using the item raises 20000010 (kind 4), the two-row bounds prompt
//
// # What this replaces, and why that was wrong
//
// The first version read vanilla `CS::ChrIns::GetMultiplayRole` and `CSSessionManager+0xc`, the two
// values the game's own dispatcher branches on. Those stay at 0 in a Seamless session even while a
// search is in flight, so the oracle predicted START unconditionally and a driver built on it
// confirmed blind -- which is how a press meant for `Both near and far` toggled the item instead.
// The other candidates are no better: `ChrIns+0x160` is a queue that never self-clears, and
// `ChrIns+0x168` counts TAE 65 consumes. None of the three reports a toggle.
const WORLD_CHR_MAN = ptr('0x143d69ff8');
const MAIN_PLAYER = 0x1e508;
const CHR_INS_SPECIAL_EFFECT = 0x178;
const SPECIAL_EFFECT_HEAD = 0x08;
const ENTRY_ID = 0x08;
const ENTRY_NEXT = 0x30;
const WALK_LIMIT = 512;
const FINGER_ACTIVE_SPEFFECT = 541;
// What the raiser reports for each prompt, so a driver can check the id it actually got against
// the one this oracle predicted instead of trusting either alone.
const MESSAGE_BOUNDS = 20000010;
const MESSAGE_LEAVE = 20000011;

function speffects () {
  const world = WORLD_CHR_MAN.readPointer();
  if (world.isNull()) return null;
  const p = world.add(MAIN_PLAYER).readPointer();
  if (p.isNull()) return null;
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
}

function variant () {
  const ids = speffects();
  if (ids === null) return { ok: false, why: 'the player SpEffect list is unreachable' };
  const active = ids.indexOf(FINGER_ACTIVE_SPEFFECT) !== -1;
  return {
    ok: true,
    active: active,
    speffects: ids,
    expectMessage: active ? MESSAGE_LEAVE : MESSAGE_BOUNDS,
    verdict: active
      ? 'the finger is ON -- using it raises "Cancel invasion of other world?" and confirming turns it OFF'
      : 'the finger is OFF -- using it raises the two-row bounds prompt',
  };
}

rpc.exports = {
  variant: variant,
  speffects: speffects,
  // A driver calls this after the raiser fires. Disagreement means the oracle is wrong for this
  // state and nothing may be confirmed.
  agrees: function (message) {
    const v = variant();
    return { ok: v.ok, expected: v.expectMessage, got: message, agrees: v.expectMessage === message };
  },
};
console.log('finger-prompt-oracle: armed on SpEffect ' + FINGER_ACTIVE_SPEFFECT);
